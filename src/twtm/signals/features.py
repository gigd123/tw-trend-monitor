"""基礎特徵：ATR、高檔位置、跳空、量能。全部只用 t 日（含）以前的資料。"""

from __future__ import annotations

import sqlite3

import numpy as np
import pandas as pd

from ..metrics.prices import load_bars


def bars_frame(conn: sqlite3.Connection, symbol: str, adjust: bool = True) -> pd.DataFrame:
    """讀取日 K 為 DataFrame（索引為交易日，Timestamp）。"""
    bars = load_bars(conn, symbol, adjust=adjust)
    df = pd.DataFrame([b.__dict__ for b in bars])
    if df.empty:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume",
                                     "turnover", "ex_date"])
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df = df.set_index("trade_date").sort_index()
    return df.dropna(subset=["open", "high", "low", "close"])


def atr(df: pd.DataFrame, n: int) -> pd.Series:
    """ATR(n) 的 t 日值，由 t-n+1..t 的 TR 平均（含 t 日）。要「至 t-1」請自行 shift。"""
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(n, min_periods=n).mean()


def add_features(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """回傳加上特徵欄位的副本。"""
    c = cfg
    out = df.copy()
    prev = out.shift(1)

    # 波動：用到 t-1 為止的 ATR 來衡量 t 日的跳空，避免跳空本身灌大分母
    out["atr_prev"] = atr(out, c["common"]["atr_n"]).shift(1)

    # 高檔（以 t-1 收盤判斷，t 日開盤前就已確定）
    n = c["high_zone"]["lookback"]
    hi = out["high"].rolling(n, min_periods=n).max().shift(1)
    lo = out["low"].rolling(n, min_periods=n).min().shift(1)
    pos = (prev["close"] - lo) / (hi - lo)
    near = prev["close"] >= hi * (1 - c["high_zone"]["near_high"])
    out["range_pos"] = pos
    out["high_zone"] = ((pos >= c["high_zone"]["quantile"]) | near) & hi.notna()

    # 跳空
    out["prev_low"] = prev["low"]
    out["gap_open"] = out["open"] < prev["low"]
    out["gap_strict"] = out["high"] < prev["low"]
    out["gap_atr"] = (prev["low"] - out["open"]) / out["atr_prev"]
    out["gap_pct"] = (prev["low"] - out["open"]) / prev["close"]
    out["close_below_prev_low"] = out["close"] < prev["low"]

    # 量能：t 日成交值 / t-1 為止的均量
    vn = c["gap"]["volume_n"]
    out["vol_ratio"] = out["turnover"] / out["turnover"].rolling(vn, min_periods=vn).mean().shift(1)

    # 量比的歷史百分位：t 日量比在 t-L..t-1 量比中的位置（gap-v2）
    lb = c["gap"].get("volume_lookback", 750)
    out["vol_pct"] = rolling_rank(out["vol_ratio"], lb, min_periods=250)

    # 均線（含 t 日）
    for n in (5, 10, 20, 60):
        out[f"ma{n}"] = out["close"].rolling(n, min_periods=n).mean()

    # 一般用途
    out["ret_1d"] = out["close"].pct_change()
    out["atr_pct_prev"] = out["atr_prev"] / prev["close"]
    out["atr_t"] = atr(out, c["common"]["atr_n"])   # 含 t 日，用於訊號日之後的回檔門檻
    return out


def rolling_rank(x: pd.Series, lookback: int, min_periods: int) -> pd.Series:
    """t 日值在 t-lookback..t-1 中的百分位（嚴格小於的比例）。只用過去資料。"""
    v = x.to_numpy(dtype=float)
    out = np.full(len(v), np.nan)
    for i in range(len(v)):
        if np.isnan(v[i]):
            continue
        past = v[max(0, i - lookback):i]
        past = past[~np.isnan(past)]
        if len(past) >= min_periods:
            out[i] = (past < v[i]).mean()
    return pd.Series(out, index=x.index)
