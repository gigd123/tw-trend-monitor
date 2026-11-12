"""讀資料 → 特徵 → 訊號事件。研究與每日訊號共用。

訊號系列（family）：
- gap：高檔跳空向下（gaps.py）
- ma：跌破均線（ma.py）
- breakout：假突破（breakouts.py）
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

import pandas as pd

from . import breakouts, gaps, ma
from .features import add_features, bars_frame

# 研究對象與其「同步市場」
PEERS = {"TAIEX": "TPEX", "TPEX": "TAIEX"}

FAMILIES = ("gap", "ma", "breakout")
TITLES = {"gap": "高檔跳空向下", "ma": "跌破均線", "breakout": "假突破"}


@dataclass
class FamilyData:
    family: str
    version: str
    f: pd.DataFrame                 # 特徵
    events: pd.DataFrame            # 訊號事件（Variant.select 的輸入）
    variants: list[gaps.Variant]
    alt_mask: pd.Series | None      # 對照基準
    alt_label: str | None


def features(conn: sqlite3.Connection, symbol: str, cfg: dict) -> pd.DataFrame:
    f = add_features(bars_frame(conn, symbol), cfg)
    if f.empty:
        raise ValueError(f"{symbol} 沒有價格資料；請先執行 twtm prices backfill")
    return f


def build(conn: sqlite3.Connection, symbol: str, cfg: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    """高檔跳空事件（保留給既有呼叫端）。"""
    d = build_family(conn, symbol, "gap", cfg)
    return d.f, d.events


def build_family(conn: sqlite3.Connection, symbol: str, family: str, cfg: dict) -> FamilyData:
    f = features(conn, symbol, cfg)
    if family == "gap":
        peer_sym = PEERS.get(symbol)
        peer = add_features(bars_frame(conn, peer_sym), cfg) if peer_sym else None
        intl = add_features(bars_frame(conn, cfg["intl"]["symbol"], adjust=False), cfg)
        events = gaps.gap_events(f, cfg, peer=peer if peer is not None and not peer.empty else None,
                                 intl=intl if not intl.empty else None)
        alt = f["high_zone"].astype(bool) & ~f.index.isin(events.index)
        return FamilyData("gap", cfg["gap"]["version"], f, events, gaps.variants(cfg),
                          pd.Series(alt, index=f.index), "高檔但未跳空的交易日")
    if family == "ma":
        events = ma.ma_events(f, cfg)
        return FamilyData("ma", cfg["ma"]["version"], f, events, ma.variants(cfg),
                          events["uptrend"].astype(bool), "多頭中（20 日線 > 60 日線）的交易日")
    if family == "breakout":
        events = breakouts.breakout_events(f, cfg)
        return FamilyData("breakout", cfg["breakout"]["version"], f, events,
                          breakouts.variants(cfg), f["high_zone"].astype(bool), "高檔的交易日")
    raise ValueError(f"未知訊號系列：{family}")
