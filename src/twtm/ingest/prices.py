"""價格資料抓取：加權、櫃買、台積電、除權息、海外指數（mvp-spec-v1.2 §3）。

與期貨模組相同的回補機制：固定日曆區段、fetch_log 記錄完成、
最近 SETTLE_DAYS 天內的區段不記完成、只有整段查過才算完成。
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Callable, Iterator

from .. import db, today_taipei, utc_now_iso
from ..http import Fetcher, RateLimited
from ..sources import finmind, tpex, twse, yahoo
from .futures import SETTLE_DAYS, Result, month_chunks, year_chunks

log = logging.getLogger(__name__)

HISTORY_START = date(2005, 1, 1)

INSTRUMENTS = [
    # symbol, name, kind, market, currency
    ("TAIEX", "發行量加權股價指數", "index", "TWSE", "TWD"),
    ("TPEX", "櫃買指數", "index", "TPEx", "TWD"),
    ("2330", "台積電", "stock", "TWSE", "TWD"),
    ("^SOX", "費城半導體指數", "index", "US", "USD"),
    ("^IXIC", "Nasdaq 綜合指數", "index", "US", "USD"),
]

PRICE_KEY = ("symbol", "trade_date")
ACTION_KEY = ("symbol", "ex_date", "action")


@dataclass(frozen=True)
class Dataset:
    source: str
    name: str                 # fetch_log 的 dataset
    key: str                  # fetch_log 的 key（標的或 '*'）
    start: date               # 此資料集可回補的最早日期
    end: date | None          # 此資料集的最晚日期（None = 至今）
    chunks: Callable[[date, date], Iterator[tuple[date, date]]]
    fetch: Callable[[Fetcher, date, date], bytes]
    parse: Callable[[bytes, str], list[dict]]
    table: str
    pkey: tuple


def datasets() -> list[Dataset]:
    m, y = month_chunks, year_chunks
    return [
        Dataset("twse", "index_ohlc", "TAIEX", HISTORY_START, None, m,
                lambda f, s, e: twse.fetch_index_ohlc(f, s), twse.parse_index_ohlc,
                "price_daily", PRICE_KEY),
        Dataset("twse", "market_turnover", "TAIEX", HISTORY_START, None, m,
                lambda f, s, e: twse.fetch_market_turnover(f, s), twse.parse_market_turnover,
                "price_daily", PRICE_KEY),
        Dataset("tpex", "index_ohlc", "TPEX", HISTORY_START, None, m,
                lambda f, s, e: tpex.fetch_index_ohlc(f, s), tpex.parse_index_ohlc,
                "price_daily", PRICE_KEY),
        Dataset("tpex", "trading_index", "TPEX", HISTORY_START, None, m,
                lambda f, s, e: tpex.fetch_trading_index(f, s), tpex.parse_trading_index,
                "price_daily", PRICE_KEY),
        Dataset("twse", "stock_day", "2330", twse.STOCK_DAY_START.replace(day=1), None, m,
                lambda f, s, e: twse.fetch_stock_day(f, "2330", s),
                lambda b, t: twse.parse_stock_day(b, "2330", t), "price_daily", PRICE_KEY),
        Dataset("finmind", "stock_price", "2330", HISTORY_START, date(2009, 12, 31), y,
                lambda f, s, e: finmind.fetch_stock_price(f, "2330", s, e),
                lambda b, t: finmind.parse_stock_price(b, "2330", t), "price_daily", PRICE_KEY),
        Dataset("twse", "ex_rights", "*", HISTORY_START, None, y,
                twse.fetch_ex_rights, twse.parse_ex_rights, "corporate_action", ACTION_KEY),
        *[Dataset("yahoo", "chart", sym, HISTORY_START, None, y,
                  lambda f, s, e, sym=sym: yahoo.fetch_chart(f, sym, s, e),
                  lambda b, t, sym=sym: yahoo.parse_chart(b, sym, t), "price_daily", PRICE_KEY)
          for sym in ("^SOX", "^IXIC")],
    ]


def seed_instruments(conn: sqlite3.Connection) -> None:
    conn.executemany(
        "INSERT INTO instrument(symbol, name, kind, market, currency) VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(symbol) DO UPDATE SET name=excluded.name, kind=excluded.kind, "
        "market=excluded.market, currency=excluded.currency", INSTRUMENTS)
    conn.commit()


def _run(conn: sqlite3.Connection, fetcher: Fetcher, ds: Dataset, chunk: tuple[date, date],
         lo: date, hi: date, force: bool, today: date, result: Result) -> None:
    c_start, c_end = (d.isoformat() for d in chunk)
    if not force and db.chunk_done(conn, ds.source, ds.name, ds.key, c_start, c_end):
        result.chunks_skipped += 1
        return
    q_start, q_end = max(chunk[0], lo), min(chunk[1], hi)
    status, n = "ok", 0
    try:
        body = ds.fetch(fetcher, q_start, q_end)
        db.save_raw(conn, ds.source, ds.name, {"key": ds.key, "start": str(q_start),
                                               "end": str(q_end)}, 200, body)
        rows = ds.parse(body, utc_now_iso())
        result.rows_written += db.upsert(conn, ds.table, rows, ds.pkey)
        n = len(rows)
        status = "ok" if n else "empty"
    except twse.OutOfRange:
        status = "out_of_window"
    result.chunks_fetched += 1
    covers = q_start <= max(chunk[0], ds.start) and q_end >= chunk[1]
    if covers and chunk[1] < today - timedelta(days=SETTLE_DAYS):
        db.log_chunk(conn, ds.source, ds.name, ds.key, c_start, c_end, n, status)
    conn.commit()
    log.info("%s %s %s %s~%s：%s，%d 筆", ds.source, ds.name, ds.key, q_start, q_end, status, n)


def backfill(conn: sqlite3.Connection, fetcher: Fetcher, start: date = HISTORY_START,
             end: date | None = None, *, only: set[str] | None = None, force: bool = False,
             today: date | None = None) -> Result:
    """回補所有價格資料集。only：限定 'source.name.key'，例如 {'twse.index_ohlc.TAIEX'}。

    單一資料集失敗（重試用盡、額度用盡）不會中止其他資料集；失敗原因記在結果裡。
    """
    today = today or today_taipei()
    end = min(end or today, today)
    seed_instruments(conn)
    result = Result()
    errors = []
    for ds in datasets():
        if only and f"{ds.source}.{ds.name}.{ds.key}" not in only:
            continue
        lo, hi = max(start, ds.start), min(end, ds.end or end)
        if lo > hi:
            continue
        try:
            for chunk in ds.chunks(lo, hi):
                _run(conn, fetcher, ds, chunk, lo, hi, force, today, result)
        except (RateLimited, Exception) as e:  # noqa: BLE001 - 單一來源失敗不拖垮全部
            conn.commit()
            errors.append(f"{ds.source}.{ds.name}.{ds.key}：{type(e).__name__}: {e}")
            log.error("%s", errors[-1])
    if errors:
        result.stopped_reason = "；".join(errors) + "（重跑會從中斷處繼續）"
    return result


def update(conn: sqlite3.Connection, fetcher: Fetcher, days: int = 35,
           today: date | None = None) -> Result:
    """每日更新：重抓最近 days 天所在的月份／年份。"""
    today = today or today_taipei()
    return backfill(conn, fetcher, today - timedelta(days=days), today, force=True, today=today)
