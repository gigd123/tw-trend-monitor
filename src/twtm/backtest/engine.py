"""減碼回測引擎（bt-v1，mvp-spec-v1.2 §6）。

時序：
- t 日收盤後決定減碼層級 T_t（0 滿倉、1 初步、2 明顯）。
- t+1 日開盤以新持股比例執行；t 日收盤到 t+1 日開盤的隔夜漲跌仍以舊持股承擔。
- 每次調整成本 = |新持股 − 舊持股| × 單邊成本。
- 未持股部分報酬為 0（不計現金利息）。

層級變化：
- 轉弱程度升高 → 立即提高減碼層級。
- 轉弱程度降低 → 符合恢復條件才降低層級（降到當時的轉弱程度）。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

RESTORE_LABELS = {
    "score3": "轉弱程度連續 3 日低於目前層級",
    "above_ma20": "收盤站回 20 日線",
    "above_ma20_ma5up": "收盤站回 20 日線，且 5 日線上揚",
}


def restore_condition(f: pd.DataFrame, rule: str) -> np.ndarray | None:
    """價格型恢復條件（每日布林）。score3 由狀態機自行計算，回傳 None。"""
    if rule == "score3":
        return None
    above = (f["close"] > f["ma20"]).to_numpy()
    if rule == "above_ma20":
        return above
    if rule == "above_ma20_ma5up":
        return above & (f["ma5"] > f["ma5"].shift(1)).to_numpy()
    raise ValueError(f"未知恢復條件：{rule}")


def tier_path(level: np.ndarray, restore_ok: np.ndarray | None, restore_days: int) -> np.ndarray:
    """由每日轉弱程度產生減碼層級（t 日收盤決定）。level 為 0..3，-1 表示資料不足（視為 0）。"""
    target = np.clip(np.where(level < 0, 0, level), 0, 2)
    tiers = np.zeros(len(target), dtype=int)
    cur, below_run = 0, 0
    for t, tgt in enumerate(target):
        if tgt > cur:
            cur, below_run = tgt, 0
        elif tgt < cur:
            below_run += 1
            ok = below_run >= restore_days if restore_ok is None else bool(restore_ok[t])
            if ok:
                cur, below_run = tgt, 0
        else:
            below_run = 0
        tiers[t] = cur
    return tiers


@dataclass
class Prices:
    """回測所需的價格陣列（同一索引）。"""
    index: pd.DatetimeIndex
    overnight: np.ndarray     # O_t / C_{t-1} − 1
    intraday: np.ndarray      # C_t / O_t − 1
    daily: np.ndarray         # C_t / C_{t-1} − 1

    @classmethod
    def from_frame(cls, f: pd.DataFrame) -> "Prices":
        c, o = f["close"].to_numpy(dtype=float), f["open"].to_numpy(dtype=float)
        prev = np.roll(c, 1)
        prev[0] = np.nan
        return cls(f.index, o / prev - 1, c / o - 1, c / prev - 1)

    def slice(self, mask: np.ndarray) -> "Prices":
        return Prices(self.index[mask], self.overnight[mask], self.intraday[mask], self.daily[mask])


@dataclass
class Run:
    net: np.ndarray           # 每日淨報酬
    gross: np.ndarray         # 每日未扣成本報酬
    cost: np.ndarray          # 每日成本
    exposure: np.ndarray      # 當日盤中持股比例
    index_ret: np.ndarray     # 大盤每日報酬


def simulate(p: Prices, tiers: np.ndarray, weights: tuple[float, float, float],
             cost: float) -> Run:
    """tiers[t] 為 t 日收盤決定的層級；weights 為（滿倉、初步、明顯）的持股比例。"""
    w = np.asarray(weights)[tiers]
    a = np.empty_like(w)                 # 第 t 日盤中持股 = t-1 日收盤的決定
    a[0] = 1.0
    a[1:] = w[:-1]
    b = np.empty_like(a)                 # 第 t 日隔夜持股 = 第 t-1 日盤中持股
    b[0] = 1.0
    b[1:] = a[:-1]
    g = np.nan_to_num(p.overnight)
    i = np.nan_to_num(p.intraday)
    gross = (1 + b * g) * (1 + a * i) - 1
    c = cost * np.abs(a - b)
    return Run(gross - c, gross, c, a, np.nan_to_num(p.daily))


def simulate_fixed(p: Prices, weight: float) -> Run:
    """全程固定持股（假設每日免費再平衡）。"""
    d = np.nan_to_num(p.daily)
    n = len(d)
    return Run(weight * d, weight * d, np.zeros(n), np.full(n, weight), d)
