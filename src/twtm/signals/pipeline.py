"""讀資料 → 特徵 → 跳空事件。研究與每日訊號共用。"""

from __future__ import annotations

import sqlite3

import pandas as pd

from .features import add_features, bars_frame
from .gaps import gap_events

# 研究對象與其「同步市場」
PEERS = {"TAIEX": "TPEX", "TPEX": "TAIEX"}


def build(conn: sqlite3.Connection, symbol: str, cfg: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    f = add_features(bars_frame(conn, symbol), cfg)
    if f.empty:
        raise ValueError(f"{symbol} 沒有價格資料；請先執行 twtm prices backfill")
    peer_sym = PEERS.get(symbol)
    peer = add_features(bars_frame(conn, peer_sym), cfg) if peer_sym else None
    intl = add_features(bars_frame(conn, cfg["intl"]["symbol"], adjust=False), cfg)
    events = gap_events(f, cfg, peer=peer if peer is not None and not peer.empty else None,
                        intl=intl if not intl.empty else None)
    return f, events
