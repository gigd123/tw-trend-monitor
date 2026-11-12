"""SQLite 儲存。

原則（規格 §15.2）：
- 原始回應（raw_fetch）與解析後資料分開保存，格式改版時可以重新解析。
- 每筆資料帶 source 與 fetched_at。
- 同一筆資料有多個來源時，依 SOURCE_PRIORITY 決定保留哪個；官方優先。
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import zlib
from pathlib import Path

from . import utc_now_iso

DEFAULT_DB = Path(os.environ.get("TWTM_DB", "data/twtm.db"))

# 數字越大越優先。低優先來源不能覆蓋高優先來源的資料。
SOURCE_PRIORITY = {"taifex": 2, "finmind": 1}

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- 每次 HTTP 請求的原始回應（zlib 壓縮）
CREATE TABLE IF NOT EXISTS raw_fetch (
    id          INTEGER PRIMARY KEY,
    source      TEXT NOT NULL,
    url         TEXT NOT NULL,
    params      TEXT NOT NULL,
    fetched_at  TEXT NOT NULL,
    http_status INTEGER NOT NULL,
    sha256      TEXT NOT NULL,
    body        BLOB NOT NULL
);

-- 回補進度：完成的查詢區段，用來中斷後續跑
CREATE TABLE IF NOT EXISTS fetch_log (
    source     TEXT NOT NULL,
    dataset    TEXT NOT NULL,
    key        TEXT NOT NULL,   -- 商品代碼或 '*'
    start_date TEXT NOT NULL,
    end_date   TEXT NOT NULL,
    rows       INTEGER NOT NULL,
    status     TEXT NOT NULL,   -- ok | empty | out_of_window
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (source, dataset, key, start_date, end_date)
);

-- 期貨三大法人：各期貨契約，口數與千元
CREATE TABLE IF NOT EXISTS fut_institutional (
    trade_date       TEXT NOT NULL,
    product          TEXT NOT NULL,  -- TX | MTX | TMF
    investor         TEXT NOT NULL,  -- dealer | trust | foreign
    long_trade_vol   INTEGER,
    long_trade_val_k INTEGER,
    short_trade_vol  INTEGER,
    short_trade_val_k INTEGER,
    long_oi          INTEGER NOT NULL,
    long_oi_val_k    INTEGER,
    short_oi         INTEGER NOT NULL,
    short_oi_val_k   INTEGER,
    source           TEXT NOT NULL,
    fetched_at       TEXT NOT NULL,
    PRIMARY KEY (trade_date, product, investor)
);

-- 期貨每日行情（不含價差組合）
CREATE TABLE IF NOT EXISTS fut_daily (
    trade_date TEXT NOT NULL,
    product    TEXT NOT NULL,
    contract   TEXT NOT NULL,  -- 202611、202611W2
    session    TEXT NOT NULL,  -- regular | after_hours
    open       REAL,
    high       REAL,
    low        REAL,
    close      REAL,
    settle     REAL,
    volume     INTEGER,
    oi         INTEGER,        -- 盤後時段為 NULL
    source     TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (trade_date, product, contract, session)
);
"""


def connect(path: Path | str = DEFAULT_DB) -> sqlite3.Connection:
    path = Path(path)
    if str(path) != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    migrate(conn)
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.execute(
        "INSERT INTO meta(key, value) VALUES('schema_version', ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (str(SCHEMA_VERSION),),
    )
    conn.commit()


def save_raw(conn: sqlite3.Connection, source: str, url: str, params: dict | None,
             status: int, body: bytes) -> int:
    cur = conn.execute(
        "INSERT INTO raw_fetch(source, url, params, fetched_at, http_status, sha256, body) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (source, url, json.dumps(params or {}, ensure_ascii=False, sort_keys=True),
         utc_now_iso(), status, hashlib.sha256(body).hexdigest(), zlib.compress(body)),
    )
    return cur.lastrowid


def load_raw(conn: sqlite3.Connection, raw_id: int) -> bytes:
    row = conn.execute("SELECT body FROM raw_fetch WHERE id=?", (raw_id,)).fetchone()
    return zlib.decompress(row["body"])


def _priority_case(table: str) -> str:
    """產生 SQL：新資料來源優先度 >= 既有資料來源優先度時才更新。"""
    cases = " ".join(f"WHEN '{s}' THEN {p}" for s, p in SOURCE_PRIORITY.items())
    return (f"(CASE excluded.source {cases} ELSE 0 END) >= "
            f"(CASE {table}.source {cases} ELSE 0 END)")


def upsert(conn: sqlite3.Connection, table: str, rows: list[dict], key: tuple[str, ...]) -> int:
    """依主鍵寫入；遵守來源優先順序。回傳實際寫入（新增或更新）的筆數。"""
    if not rows:
        return 0
    cols = list(rows[0].keys())
    updates = ", ".join(f"{c}=excluded.{c}" for c in cols if c not in key)
    sql = (
        f"INSERT INTO {table}({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))}) "
        f"ON CONFLICT({', '.join(key)}) DO UPDATE SET {updates} WHERE {_priority_case(table)}"
    )
    before = conn.total_changes
    conn.executemany(sql, [tuple(r[c] for c in cols) for r in rows])
    return conn.total_changes - before


def log_chunk(conn: sqlite3.Connection, source: str, dataset: str, key: str,
              start: str, end: str, rows: int, status: str) -> None:
    conn.execute(
        "INSERT INTO fetch_log VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(source, dataset, key, start_date, end_date) DO UPDATE SET "
        "rows=excluded.rows, status=excluded.status, fetched_at=excluded.fetched_at",
        (source, dataset, key, start, end, rows, status, utc_now_iso()),
    )


def chunk_done(conn: sqlite3.Connection, source: str, dataset: str, key: str,
               start: str, end: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM fetch_log WHERE source=? AND dataset=? AND key=? "
        "AND start_date=? AND end_date=?",
        (source, dataset, key, start, end),
    ).fetchone() is not None
