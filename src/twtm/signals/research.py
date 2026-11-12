"""訊號研究（事件研究法，mvp-spec-v1.2 §4.3）。

對每個訊號變體：
- 訊號日收盤後 1/3/5/10/20 日報酬、20 日內最大不利跌幅（MAE）
- 20 日內大回檔機率：最低價 <= 收盤 − dd_atr × ATR（主要）、回檔 >= dd_pct（輔助）
- 與「同期間所有交易日」比較；另可指定一個對照基準（例如高檔未跳空、多頭中）
- 探索期 / 驗證期分開；去叢集；自助抽樣 p 值

判定規則依 research.verdict_metric：
- returns（research-v1）：看 5 日、20 日平均報酬是否兩期都比基準差
- drawdown（research-v2）：看 20 日內大回檔機率是否兩期都比基準高
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .gaps import Variant, signal_dates


def forward_metrics(f: pd.DataFrame, horizons: list[int], mae_days: int,
                    dd_atr: float = 4.0, dd_pct: float = 0.05) -> pd.DataFrame:
    out = pd.DataFrame(index=f.index)
    for h in horizons:
        out[f"r{h}"] = f["close"].shift(-h) / f["close"] - 1
    fut_min = f["low"].shift(-1)[::-1].rolling(mae_days, min_periods=mae_days).min()[::-1]
    out["mae"] = fut_min / f["close"] - 1
    known = fut_min.notna()
    atr_t = f["atr_t"] if "atr_t" in f else pd.Series(np.nan, index=f.index)
    dd = (f["close"] - fut_min) >= dd_atr * atr_t
    out["dd"] = dd.astype(float).where(known & atr_t.notna())
    out["dd5"] = (out["mae"] <= -dd_pct).astype(float).where(known)
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
    mean: dict[str, float] = field(default_factory=dict)       # 去叢集後平均（dd 為機率）
    pct_down: dict[str, float] = field(default_factory=dict)   # 報酬 < 0 的比例
    base: dict[str, float] = field(default_factory=dict)       # 所有交易日平均
    diff_all: dict[str, float] = field(default_factory=dict)   # 減所有交易日平均
    diff_alt: dict[str, float] = field(default_factory=dict)   # 減對照基準平均
    p_all: dict[str, float] = field(default_factory=dict)      # 報酬：隨機平均 <= 訊號；dd：隨機 >= 訊號


def _bootstrap(pool: np.ndarray, n: int, value: float, iters: int,
               rng: np.random.Generator, greater: bool) -> float:
    pool = pool[~np.isnan(pool)]
    if n == 0 or len(pool) < n:
        return float("nan")
    means = rng.choice(pool, size=(iters, n), replace=True).mean(axis=1)
    return float((means >= value).mean() if greater else (means <= value).mean())


METRIC_COLS = ("mae", "dd", "dd5")


def _rng(cfg: dict, *keys: str) -> np.random.Generator:
    """亂數只由種子與（變體、期間、指標）決定，新增指標或變體不會改變既有結果。"""
    import zlib
    return np.random.default_rng([cfg["research"]["seed"],
                                  *(zlib.crc32(k.encode()) for k in keys)])


def period_stats(dates: pd.DatetimeIndex, fwd: pd.DataFrame, base: pd.DatetimeIndex,
                 alt: pd.DatetimeIndex | None, calendar: pd.DatetimeIndex, cfg: dict,
                 key: tuple[str, str] = ("", "")) -> PeriodStats:
    rc = cfg["research"]
    cols = [f"r{h}" for h in rc["horizons"]] + list(METRIC_COLS)
    dc = decluster(dates, calendar, rc["decluster_days"])
    st = PeriodStats(n=len(dates), n_dc=len(dc))
    sub = fwd.loc[dc, cols]
    for c in cols:
        x = sub[c].dropna()
        st.base[c] = float(fwd.loc[base, c].mean())
        if x.empty:
            continue
        st.mean[c] = float(x.mean())
        st.pct_down[c] = float((x < 0).mean())
        st.diff_all[c] = st.mean[c] - st.base[c]
        if alt is not None and len(alt):
            st.diff_alt[c] = st.mean[c] - float(fwd.loc[alt, c].mean())
        greater = c in ("dd", "dd5")
        st.p_all[c] = _bootstrap(fwd.loc[base, c].to_numpy(), len(x), st.mean[c],
                                 rc["bootstrap"], _rng(cfg, *key, c), greater)
    return st


@dataclass
class VariantResult:
    variant: Variant
    periods: dict[str, PeriodStats]     # "discovery" | "validation" | "all"
    verdict: str


def verdict_returns(disc: PeriodStats, val: PeriodStats, min_n: int) -> str:
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


def verdict_drawdown(disc: PeriodStats, val: PeriodStats, min_n: int) -> str:
    if disc.n_dc < min_n or val.n_dc < min_n:
        return "樣本不足"
    up_d = disc.diff_all.get("dd", 0) > 0
    up_v = val.diff_all.get("dd", 0) > 0
    if up_d and up_v:
        strong = disc.p_all.get("dd", 1) < 0.10 and val.p_all.get("dd", 1) < 0.10
        return "兩期回檔機率皆較高（顯著）" if strong else "兩期回檔機率皆較高"
    if up_d != up_v:
        return "兩期不一致"
    return "未提高"


VERDICTS = {"returns": verdict_returns, "drawdown": verdict_drawdown}


def run(f: pd.DataFrame, events: pd.DataFrame, vs: list[Variant], cfg: dict,
        alt_mask: pd.Series | None = None) -> list[VariantResult]:
    """alt_mask：對照基準（布林，索引同 f）；None 表示只和所有交易日比較。"""
    rc = cfg["research"]
    fwd = forward_metrics(f, rc["horizons"], rc["mae_days"], rc.get("dd_atr", 4.0),
                          rc.get("dd_pct", 0.05))
    calendar = f.index
    split = pd.Timestamp(rc["split_date"])
    valid = (f["atr_prev"].notna() & f["high_zone"].notna()).to_numpy()
    periods = {
        "discovery": calendar[(calendar < split) & valid],
        "validation": calendar[(calendar >= split) & valid],
    }
    periods["all"] = periods["discovery"].append(periods["validation"])
    judge = VERDICTS[rc.get("verdict_metric", "returns")]

    results = []
    for v in vs:
        dates = signal_dates(events, v)
        per = {}
        for name, cal in periods.items():
            d = dates[dates.isin(cal)]
            alt = None
            if alt_mask is not None:
                alt = cal[alt_mask.reindex(cal).fillna(False).astype(bool).to_numpy()]
            per[name] = period_stats(d, fwd, cal, alt, calendar, cfg, (v.id, name))
        results.append(VariantResult(v, per, judge(per["discovery"], per["validation"],
                                                   rc["min_events"])))
    return results
