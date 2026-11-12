"""假突破系列（fb-v1）。每個交易日一列，布林欄位表示當日訊號成立。

- B1 盤中假突破：最高價 > 前 N 日最高，收盤 < 前 N 日最高。
- B2 收盤突破失敗：某日收盤突破前 N 日最高（突破位 L），之後 fail_days 日內收盤 < L；
  訊號日為收回的那天。
- B3 二次創高假突破：B1 或 B2，且被突破的高點形成於突破日前 >= retest_gap 日，
  之間最低價曾比該高點低 >= retest_pullback_atr 個 ATR。
- B4 假突破後向下跳空：B1 或 B2 之後 gap_within 日內出現開盤跳空向下（訊號日為跳空日）。
- B1L / B2L：同 B1 / B2，改用前 lookback_long 日最高。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .gaps import Variant


def _prior_high(high: np.ndarray, n: int) -> tuple[np.ndarray, np.ndarray]:
    """前 n 日（t-n..t-1）最高價與其所在位置；資料不足為 nan / -1。"""
    ph = np.full(len(high), np.nan)
    pos = np.full(len(high), -1)
    for i in range(n, len(high)):
        window = high[i - n:i]
        j = int(np.argmax(window))
        ph[i], pos[i] = window[j], i - n + j
    return ph, pos


def _scan(f: pd.DataFrame, n: int, cfg: dict) -> dict[str, np.ndarray]:
    c = cfg["breakout"]
    high, low, close = (f[k].to_numpy() for k in ("high", "low", "close"))
    atr_prev = f["atr_prev"].to_numpy()
    ph, ph_pos = _prior_high(high, n)
    size = len(f)
    b1, b2, b3 = (np.zeros(size, bool) for _ in range(3))
    strength = np.full(size, np.nan)

    def retest(s: int) -> bool:
        """突破日 s 所突破的高點，是否為「拉回後再度創高」。"""
        hp = ph_pos[s]
        if hp < 0 or s - hp < c["retest_gap"] or np.isnan(atr_prev[s]):
            return False
        between = low[hp + 1:s]
        return between.size > 0 and between.min() <= ph[s] - c["retest_pullback_atr"] * atr_prev[s]

    for t in range(size):
        if np.isnan(ph[t]):
            continue
        # B1：盤中突破、收盤收回
        if high[t] > ph[t] and close[t] < ph[t]:
            b1[t] = True
            strength[t] = (ph[t] - close[t]) / atr_prev[t] if atr_prev[t] else np.nan
            if retest(t):
                b3[t] = True
        # B2：t 日收盤突破 → 之後 fail_days 日內收回突破位以下
        if close[t] > ph[t]:
            level = ph[t]
            for u in range(t + 1, min(t + 1 + c["fail_days"], size)):
                if close[u] < level:
                    b2[u] = True
                    if np.isnan(strength[u]) and atr_prev[u]:
                        strength[u] = (level - close[u]) / atr_prev[u]
                    if retest(t):
                        b3[u] = True
                    break
    return {"b1": b1, "b2": b2, "b3": b3, "strength": strength}


def breakout_events(f: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    c = cfg["breakout"]
    short = _scan(f, c["lookback"], cfg)
    long_ = _scan(f, c["lookback_long"], cfg)
    e = pd.DataFrame(index=f.index)
    e["B1"], e["B2"], e["B3"] = short["b1"], short["b2"], short["b3"]
    e["B1L"], e["B2L"] = long_["b1"], long_["b2"]

    fb = (e["B1"] | e["B2"]).to_numpy()
    gap = (f["gap_open"] & ~f["ex_date"].astype(bool)).to_numpy()
    w = c["gap_within"]
    b4 = np.zeros(len(f), bool)
    for g in np.flatnonzero(gap):
        if fb[max(0, g - w):g].any():
            b4[g] = True
    e["B4"] = b4
    e["strength"] = np.where(b4, f["gap_atr"].to_numpy(), short["strength"])
    return e


def variants(cfg: dict) -> list[Variant]:
    c = cfg["breakout"]
    n, nl = c["lookback"], c["lookback_long"]
    col = lambda name: (lambda e: e[name].astype(bool))  # noqa: E731
    return [
        Variant("B1_intraday", f"盤中突破前 {n} 日高點、收盤收回", col("B1")),
        Variant("B2_close_fail", f"收盤突破前 {n} 日高點，{c['fail_days']} 日內收回", col("B2")),
        Variant("B3_retest", f"二次創高假突破（高點 ≥ {c['retest_gap']} 日前、"
                f"其間回落 ≥ {c['retest_pullback_atr']} ATR）", col("B3")),
        Variant("B4_fb_then_gap", f"假突破後 {c['gap_within']} 日內向下跳空（訊號日為跳空日）", col("B4")),
        Variant("B1L_intraday", f"盤中突破前 {nl} 日高點、收盤收回", col("B1L")),
        Variant("B2L_close_fail", f"收盤突破前 {nl} 日高點，{c['fail_days']} 日內收回", col("B2L")),
    ]
