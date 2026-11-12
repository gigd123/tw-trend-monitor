"""前向紀錄：每日收盤後記錄轉弱程度與各減碼組合的層級。

- 只新增、不覆寫（INSERT OR IGNORE）：保留「當時知道的狀態」。
- 記錄所有候選組合（bt-v1 的 27 組、bt-v2 的 9 組），之後可用前向資料評估任何一組，
  不必事先挑選。
- 超過下一個交易日 09:00 才記錄的列標記為補記（late=1）。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, time, timedelta

import pandas as pd

from .. import TAIPEI, utc_now_iso
from ..backtest.engine import restore_condition, tier_path
from ..backtest.study import grid
from ..signals import DEFAULT_CONFIG, load_config
from ..signals.pipeline import features
from .levels import price_weakness

FORWARD_START = date(2026, 11, 12)   # 研究資料截止於 2026-11-11
BT_CONFIGS = [DEFAULT_CONFIG.with_name("bt-v1.toml"), DEFAULT_CONFIG]


def _late(trade_date: date, now: datetime) -> bool:
    nxt = trade_date + timedelta(days=1)
    while nxt.weekday() >= 5:
        nxt += timedelta(days=1)
    return now >= datetime.combine(nxt, time(9, 0), TAIPEI)


def record(conn: sqlite3.Connection, symbols=("TAIEX", "TPEX"), cfg: dict | None = None,
           start: date = FORWARD_START, now: datetime | None = None) -> list[str]:
    cfg = cfg or load_config()
    now = now or datetime.now(TAIPEI)
    bt_cfgs = [load_config(p) for p in BT_CONFIGS if p.exists()]
    out = []
    for sym in symbols:
        f = features(conn, sym, cfg)
        lv = price_weakness(f, cfg)
        level = lv["level"].fillna(-1).astype(int).to_numpy()
        tiers: dict[str, pd.Series] = {}
        for bc in bt_cfgs:
            b = bc["backtest"]
            for combo in grid(bc):
                t = tier_path(level, restore_condition(f, combo.restore), b["restore_days"])
                tiers[f"{b['version']}|{combo.id}"] = pd.Series(t, index=f.index)
        dates = f.index[f.index >= pd.Timestamp(start)]
        for d in dates:
            r = lv.loc[d]
            row = (d.date().isoformat(), sym, cfg["risk"]["version"],
                   None if pd.isna(r["level"]) else int(r["level"]), r["label"],
                   int(r["A"]), int(r["B"]), int(r["C"]), float(f.loc[d, "close"]),
                   json.dumps({k: int(v.loc[d]) for k, v in tiers.items()}),
                   utc_now_iso(), int(_late(d.date(), now)))
            cur = conn.execute(
                "INSERT OR IGNORE INTO forward_log VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", row)
            if cur.rowcount:
                out.append(f"{sym} {row[0]}：{row[4]}" + ("（補記）" if row[-1] else ""))
    conn.commit()
    return out


def recent(conn: sqlite3.Connection, days: int = 20) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM forward_log ORDER BY trade_date DESC, symbol LIMIT ?", (days * 2,)).fetchall()
