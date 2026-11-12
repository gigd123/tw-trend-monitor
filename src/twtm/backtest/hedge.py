"""期貨避險回測（hedge-v1，mvp-spec-v1.2 §6.8）。

股票部位一直 100%；明顯轉弱時放空台指期（名目 = 組合市值 × 避險比例）。

期貨損益使用實際成交價：
- 只用一般時段月合約。合約的最後交易日 = 資料中該合約最後出現的交易日。
- 第 t 日持有的合約 = 最後交易日 > t 的最近月合約。
  因此在最後交易日的前一日收盤轉倉，最後交易日當天已持有次月。
- 第 t 日期貨報酬分成隔夜（前一日收盤 → 今日開盤）與盤中（開盤 → 收盤），
  都用「第 t 日持有的合約」計算；轉倉後的新合約前一日也有收盤價。

時序與減碼回測相同：t 日收盤決定避險層級，t+1 日開盤調整。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .engine import Prices, Run


@dataclass
class FuturesSeries:
    """與現貨日期對齊的近月期貨報酬。"""
    index: pd.DatetimeIndex
    contract: np.ndarray       # 第 t 日持有的合約（YYYYMM）
    overnight: np.ndarray      # 持有合約：前一日收盤 → 今日開盤
    intraday: np.ndarray       # 持有合約：今日開盤 → 收盤
    roll_after: np.ndarray     # 第 t 日收盤後轉倉（第 t+1 日持有不同合約）
    close: np.ndarray          # 持有合約今日收盤

    def slice(self, mask: np.ndarray) -> "FuturesSeries":
        return FuturesSeries(self.index[mask], self.contract[mask], self.overnight[mask],
                             self.intraday[mask], self.roll_after[mask], self.close[mask])


def load_futures(conn: sqlite3.Connection, product: str = "TX") -> pd.DataFrame:
    rows = conn.execute(
        "SELECT trade_date, contract, open, close FROM fut_daily "
        "WHERE product=? AND session='regular' AND contract NOT LIKE '%W%' "
        "AND contract NOT LIKE '%/%' AND close IS NOT NULL ORDER BY trade_date",
        (product,)).fetchall()
    df = pd.DataFrame([dict(r) for r in rows])
    if df.empty:
        raise ValueError(f"沒有 {product} 期貨行情；請先執行 "
                         f"twtm futures backfill --dataset daily --product {product} --start 2005-01-01")
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df["contract"] = df["contract"].str.strip()
    return df


def third_wednesday(contract: str) -> pd.Timestamp:
    """台指期月合約的最後交易日規則：契約月份的第三個星期三（遇假日順延，此處不處理）。"""
    first = pd.Timestamp(int(contract[:4]), int(contract[4:6]), 1)
    offset = (2 - first.weekday()) % 7          # 第一個星期三
    return first + pd.Timedelta(days=offset + 14)


def contract_expiry(fut: pd.DataFrame) -> pd.Series:
    """各合約最後交易日。已到期：資料中最後一個交易日；尚在交易（最後交易日＝資料最後一天）：依規則推算。"""
    last = fut.groupby("contract")["trade_date"].max()
    data_end = fut["trade_date"].max()
    live = last[last == data_end].index
    for c in live:
        last[c] = max(third_wednesday(c), data_end)
    return last


def front_month(fut: pd.DataFrame, dates: pd.DatetimeIndex) -> FuturesSeries:
    expiry = contract_expiry(fut)
    close = fut.pivot_table(index="trade_date", columns="contract", values="close")
    opn = fut.pivot_table(index="trade_date", columns="contract", values="open")
    exp_sorted = expiry.sort_values()
    n = len(dates)
    held = np.empty(n, dtype=object)
    for k, d in enumerate(dates):
        later = exp_sorted[exp_sorted > d]
        held[k] = later.index[0] if len(later) else None

    ov = np.full(n, np.nan)
    intra = np.full(n, np.nan)
    cl = np.full(n, np.nan)
    cidx = {d: i for i, d in enumerate(close.index)}
    for k, d in enumerate(dates):
        c = held[k]
        if c is None or d not in cidx or c not in close.columns:
            continue
        i = cidx[d]
        today_c, today_o = close.iat[i, close.columns.get_loc(c)], opn.iat[i, opn.columns.get_loc(c)]
        cl[k] = today_c
        if i > 0:
            prev_c = close.iat[i - 1, close.columns.get_loc(c)]
            if not np.isnan(prev_c) and not np.isnan(today_o):
                ov[k] = today_o / prev_c - 1
        if not np.isnan(today_o) and today_o:
            intra[k] = today_c / today_o - 1
    roll = np.zeros(n, bool)
    roll[:-1] = held[:-1] != held[1:]
    return FuturesSeries(dates, held, ov, intra, roll, cl)


def simulate_hedge(stock_ret: np.ndarray, fs: FuturesSeries, tiers: np.ndarray,
                   hedge_by_tier: tuple[float, float, float], cost: float,
                   spot: Prices | None = None) -> Run:
    """股票 100%（每日報酬 stock_ret）＋ 依層級放空。

    spot 為 None：放空近月期貨（實際價格，含轉倉）。
    spot 有值：對照組，放空現貨價格指數（沒有正逆價差、沒有轉倉）。
    回傳的 exposure 為淨曝險（1 − 避險比例）；index_ret 為股票部位報酬。
    """
    h = np.asarray(hedge_by_tier)[tiers]
    a = np.empty_like(h)          # 第 t 日盤中避險比例（t-1 收盤決定）
    a[0] = 0.0
    a[1:] = h[:-1]
    b = np.empty_like(a)          # 第 t 日隔夜避險比例
    b[0] = 0.0
    b[1:] = a[:-1]
    if spot is None:
        g, i = np.nan_to_num(fs.overnight), np.nan_to_num(fs.intraday)
    else:
        g, i = np.nan_to_num(spot.overnight), np.nan_to_num(spot.intraday)
    s_ret = np.nan_to_num(stock_ret)
    gross = s_ret - (b * g + a * i)
    c = cost * np.abs(a - b)
    if spot is None:                                   # 第 t 日收盤轉倉（兩腳），記在第 t 日
        roll_cost = np.zeros_like(a)
        roll_cost[:-1] = 2 * cost * a[:-1] * fs.roll_after[:-1]
        c = c + roll_cost
    return Run(gross - c, gross, c, 1 - a, s_ret)


def basis_effect(spot: Prices, fs: FuturesSeries, a: np.ndarray) -> dict:
    """避險期間「期貨報酬 − 現貨價格指數報酬」× 避險比例的累計（正值＝放空期貨比放空現貨吃虧）。

    除息旺季（6–9 月）與其他月份分開，用來看股利造成的逆價差影響。
    """
    fut_ret = (1 + np.nan_to_num(fs.overnight)) * (1 + np.nan_to_num(fs.intraday)) - 1
    spot_ret = np.nan_to_num(spot.daily)
    diff = (fut_ret - spot_ret) * a
    months = spot.index.month
    season = (months >= 6) & (months <= 9)
    yrs = max((spot.index[-1] - spot.index[0]).days / 365.25, 1e-9)
    return {
        "hedged_days": int((a > 0).sum()),
        "total": float(diff.sum()),
        "season": float(diff[season].sum()),
        "other": float(diff[~season].sum()),
        "per_year": float(diff.sum() / yrs),
        "rolls": int((fs.roll_after & (a > 0)).sum()),
    }
