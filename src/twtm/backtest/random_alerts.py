"""條件相同的隨機警報（mvp-spec-v1.2 §6.4）。

把策略的減碼層級路徑切成「減碼段」（層級 > 0 的連續日，保留段內每天的層級），
只隨機移動每段的起始日、段與段不重疊。因此警報次數、每段持續天數、減碼幅度、
平均持股比例、切換次數與交易成本都和策略相同。

起始日抽樣兩種：
- unconditional：任何交易日
- down_day：只從收盤下跌的交易日（策略的減碼一定發生在下跌之後；
  這個版本檢驗訊號是否比「下跌後隨便減碼」更有價值）
"""

from __future__ import annotations

import numpy as np


def episodes(tiers: np.ndarray) -> list[np.ndarray]:
    out, t = [], 0
    while t < len(tiers):
        if tiers[t] > 0:
            s = t
            while t < len(tiers) and tiers[t] > 0:
                t += 1
            out.append(tiers[s:t].copy())
        else:
            t += 1
    return out


def relocate(eps: list[np.ndarray], n: int, allowed: np.ndarray,
             rng: np.random.Generator, max_tries: int = 20) -> np.ndarray | None:
    """把各段放到不重疊（至少間隔 1 日）的隨機位置。放不下回傳 None。"""
    for _ in range(max_tries):
        out = np.zeros(n, dtype=int)
        occ = np.zeros(n + 2, dtype=int)       # occ[k+1] 對應第 k 日
        ok = True
        for ep in sorted(eps, key=len, reverse=True):   # 長的先放，降低放不下的機率
            L = len(ep)
            if L > n:
                return None
            cum = np.concatenate([[0], np.cumsum(occ)])
            starts = np.arange(0, n - L + 1)
            # 視窗 [s-1, s+L] 都必須空著
            win = cum[starts + L + 2] - cum[starts]
            cand = starts[(win == 0) & allowed[starts]]
            if cand.size == 0:
                ok = False
                break
            s = int(rng.choice(cand))
            out[s:s + L] = ep
            occ[s + 1:s + L + 1] = 1
        if ok:
            return out
    return None
