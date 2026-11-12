"""把訊號事件寫入 signal_event。同一版本重算時整批取代。"""

from __future__ import annotations

import json
import sqlite3

import pandas as pd

from .. import utc_now_iso
from .gaps import Variant


def _clean(v):
    if v is None or (not isinstance(v, str) and pd.isna(v)):
        return None
    if hasattr(v, "item"):
        return v.item()
    if isinstance(v, pd.Timestamp):
        return v.date().isoformat()
    return v


DETAIL_COLS = ["gap_atr", "gap_pct", "gap_strict", "close_below_prev_low", "vol_ratio",
               "range_pos", "peer_sync", "intl_sync"]


def save_events(conn: sqlite3.Connection, scope: str, symbol: str, events: pd.DataFrame,
                vs: list[Variant], version: str) -> int:
    now = utc_now_iso()
    rows = []
    for v in vs:
        sel = events[v.select(events)]
        for anchor, e in sel.iterrows():
            sig = anchor if v.signal_col is None else e[v.signal_col]
            if pd.isna(sig):
                continue
            detail = {c: _clean(e.get(c)) for c in DETAIL_COLS if c in e}
            rows.append((scope, symbol, v.id, version, sig.date().isoformat(),
                         anchor.date().isoformat(), _clean(e.get("strength")),
                         json.dumps(detail, ensure_ascii=False), now))
    conn.execute("DELETE FROM signal_event WHERE scope=? AND symbol=? AND rule_version=?",
                 (scope, symbol, version))
    conn.executemany("INSERT INTO signal_event VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
    conn.commit()
    return len(rows)
