"""回測績效指標。"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .engine import Run


@dataclass
class Metrics:
    cagr: float
    max_dd: float             # 負值
    calmar: float             # 年化報酬 / |最大回撤|
    vol: float
    avg_exposure: float
    switches_per_year: float  # 持股比例變動次數 / 年
    reductions_per_year: float  # 由滿倉進入減碼的次數 / 年
    missed_gain: float        # 減碼期間大盤上漲而少賺（每段加總，正值）
    avoided_loss: float       # 減碼期間大盤下跌而避開（每段加總，正值）
    cost: float               # 交易成本合計（報酬率加總）
    episodes: int
    episodes_avoided: int     # 減碼期間大盤下跌的段數


def years(index: pd.DatetimeIndex) -> float:
    return max((index[-1] - index[0]).days / 365.25, 1e-9)


def max_drawdown(net: np.ndarray) -> float:
    eq = np.cumprod(1 + net)
    peak = np.maximum.accumulate(np.concatenate([[1.0], eq]))[1:]
    return float((eq / peak - 1).min())


def compute(run: Run, index: pd.DatetimeIndex) -> Metrics:
    yrs = years(index)
    eq = np.cumprod(1 + run.net)
    cagr = float(eq[-1] ** (1 / yrs) - 1)
    mdd = max_drawdown(run.net)
    vol = float(np.std(run.net) * np.sqrt(252))
    a = run.exposure
    changes = np.abs(np.diff(a)) > 1e-12
    entering = (a[1:] < 1 - 1e-12) & (a[:-1] >= 1 - 1e-12)

    # 減碼段：盤中持股 < 1 的連續日；每段比較大盤與組合（未扣成本）
    forgone = run.index_ret - run.gross
    in_ep = a < 1 - 1e-12
    missed = avoided = 0.0
    n_ep = n_avoid = 0
    t = 0
    while t < len(a):
        if in_ep[t]:
            s = t
            while t < len(a) and in_ep[t]:
                t += 1
            x = float(forgone[s:t].sum())
            n_ep += 1
            if x > 0:
                missed += x
            else:
                avoided += -x
                n_avoid += 1
        else:
            t += 1
    return Metrics(cagr, mdd, cagr / abs(mdd) if mdd < 0 else float("nan"), vol,
                   float(a.mean()), float(changes.sum() / yrs), float(entering.sum() / yrs),
                   missed, avoided, float(run.cost.sum()), n_ep, n_avoid)
