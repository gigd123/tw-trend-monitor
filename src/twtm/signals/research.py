"""訊號研究（事件研究法，mvp-spec-v1.2 §4.3）。

對每個訊號變體：
- 訊號日收盤後 1/3/5/10/20 日報酬、20 日內最大不利跌幅（MAE）
- 與「同期間所有交易日」及「高檔但未跳空的交易日」比較
- 探索期 / 驗證期分開
- 去叢集：保留訊號後 decluster_days 個交易日內不再計入新訊號
- 自助抽樣：從基準池抽同樣數量的日子，看平均報酬低於訊號的機率
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .gaps import Variant, signal_dates


def forward_metrics(f: pd.DataFrame, horizons: list[int], mae_days: int) -> pd.DataFrame:
    out = pd.DataFrame(index=f.index)
    for h in horizons:
        out[f"r{h}"] = f["close"].shift(-h) / f["close"] - 1
    fut_min = f["low"].shift(-1)[::-1].rolling(mae_days, min_periods=mae_days).min()[::-1]
    out["mae"] = fut_min / f["close"] - 1
    return out


def decluster(dates: pd.DatetimeIndex, calendar: pd.DatetimeIndex, gap: int) -> pd.DatetimeIndex:
    pos = calendar.get_indexer(dates)
    kept, last = [], None
    for d, p in zip(dates, pos):
        if p < 0:
            continue
        if last is None or p - last > gap:
            kept.append(d)
            last = p
    return pd.DatetimeIndex(kept)


@dataclass
class PeriodStats:
    n: int                      # 原始訊號數
    n_dc: int                   # 去叢集後
    mean: dict[str, float] = field(default_factory=dict)       # 去叢集後平均
    pct_down: dict[str, float] = field(default_factory=dict)   # 報酬 < 0 的比例
    diff_all: dict[str, float] = field(default_factory=dict)   # 減「所有交易日」平均
    diff_hz: dict[str, float] = field(default_factory=dict)    # 減「高檔未跳空」平均
    p_all: dict[str, float] = field(default_factory=dict)      # 隨機日平均 <= 訊號平均 的機率


def _bootstrap_p(pool: np.ndarray, n: int, value: float, iters: int,
                 rng: np.random.Generator) -> float:
    pool = pool[~np.isnan(pool)]
    if n == 0 or len(pool) < n:
        return float("nan")
    means = rng.choice(pool, size=(iters, n), replace=True).mean(axis=1)
    return float((means <= value).mean())


def period_stats(dates: pd.DatetimeIndex, fwd: pd.DataFrame, base_all: pd.DatetimeIndex,
                 base_hz: pd.DatetimeIndex, calendar: pd.DatetimeIndex, cfg: dict,
                 rng: np.random.Generator) -> PeriodStats:
    rc = cfg["research"]
    cols = [f"r{h}" for h in rc["horizons"]] + ["mae"]
    dc = decluster(dates, calendar, rc["decluster_days"])
    st = PeriodStats(n=len(dates), n_dc=len(dc))
    sub = fwd.loc[dc, cols]
    for c in cols:
        x = sub[c].dropna()
        if x.empty:
            continue
        st.mean[c] = float(x.mean())
        st.pct_down[c] = float((x < 0).mean())
        st.diff_all[c] = st.mean[c] - float(fwd.loc[base_all, c].mean())
        st.diff_hz[c] = st.mean[c] - float(fwd.loc[base_hz, c].mean())
        st.p_all[c] = _bootstrap_p(fwd.loc[base_all, c].to_numpy(), len(x), st.mean[c],
                                   rc["bootstrap"], rng)
    return st


@dataclass
class VariantResult:
    variant: Variant
    periods: dict[str, PeriodStats]     # "discovery" | "validation" | "all"
    verdict: str


def verdict(disc: PeriodStats, val: PeriodStats, min_n: int) -> str:
    """事先固定的判定規則（報告開頭會列出）。"""
    if disc.n_dc < min_n or val.n_dc < min_n:
        return "樣本不足"
    keys = ("r5", "r20")
    neg_d = all(disc.diff_all.get(k, 0) < 0 for k in keys)
    neg_v = all(val.diff_all.get(k, 0) < 0 for k in keys)
    if neg_d and neg_v:
        strong = disc.p_all.get("r20", 1) < 0.10 and val.p_all.get("r20", 1) < 0.10
        return "兩期一致偏弱（顯著）" if strong else "兩期一致偏弱"
    if neg_d != neg_v:
        return "兩期不一致"
    return "未偏弱"


def run(f: pd.DataFrame, events: pd.DataFrame, vs: list[Variant], cfg: dict) -> list[VariantResult]:
    rc = cfg["research"]
    rng = np.random.default_rng(rc["seed"])
    fwd = forward_metrics(f, rc["horizons"], rc["mae_days"])
    calendar = f.index
    split = pd.Timestamp(rc["split_date"])
    valid = f["atr_prev"].notna() & f["high_zone"].notna()
    gap_days = set(events.index)
    periods = {
        "discovery": calendar[(calendar < split) & valid.to_numpy()],
        "validation": calendar[(calendar >= split) & valid.to_numpy()],
    }
    periods["all"] = periods["discovery"].append(periods["validation"])

    results = []
    for v in vs:
        dates = signal_dates(events, v)
        per = {}
        for name, cal in periods.items():
            d = dates[dates.isin(cal)]
            hz = cal[f.loc[cal, "high_zone"].astype(bool).to_numpy()]
            hz = hz[~hz.isin(gap_days)]
            per[name] = period_stats(d, fwd, cal, hz, calendar, cfg, rng)
        results.append(VariantResult(v, per, verdict(per["discovery"], per["validation"],
                                                     rc["min_events"])))
    return results
