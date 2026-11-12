"""期貨避險回測研究（hedge-v1）。"""

from __future__ import annotations

import itertools
import sqlite3
import zlib
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..risk.levels import price_weakness
from ..signals.features import add_features, bars_frame
from .engine import Prices, Run, restore_condition, tier_path
from .hedge import FuturesSeries, basis_effect, front_month, load_futures, simulate_hedge
from .metrics import Metrics, compute
from .random_alerts import episodes, relocate
from .study import RandomStats, stress_windows

PERIOD_LABELS = {"full": "全期", "selection": "選參期", "test": "檢驗期"}


@dataclass(frozen=True)
class HedgeCombo:
    ratio: float
    restore: str

    @property
    def id(self) -> str:
        return f"{self.ratio:.0%}/{self.restore}"


@dataclass
class HedgeResult:
    combo: HedgeCombo
    metrics: dict[str, Metrics]
    fixed: dict[str, Metrics]        # 全程固定避險（＝策略平均避險比例）
    ma_rule: dict[str, Metrics]      # 跌破 60 日線才避險
    spot: dict[str, Metrics]         # 對照：放空現貨價格指數
    random: dict[str, dict[str, RandomStats]] = field(default_factory=dict)


@dataclass
class HedgeStudy:
    symbol: str
    stock_label: str
    cfg: dict
    f: pd.DataFrame
    periods: dict[str, np.ndarray]
    unhedged: dict[str, Metrics]
    results: list[HedgeResult]
    selected: HedgeResult
    basis: dict
    stress: pd.DataFrame
    yearly: pd.DataFrame
    cost_sens: list[tuple[float, Metrics]]
    coverage: dict


def _rng(seed: int, *keys: str) -> np.random.Generator:
    return np.random.default_rng([seed, *(zlib.crc32(k.encode()) for k in keys)])


def stock_returns(conn: sqlite3.Connection, symbol: str, index: pd.DatetimeIndex) -> tuple[np.ndarray, str]:
    """加權用報酬指數（含息）；櫃買用價格指數（找不到報酬指數歷史）。"""
    if symbol == "TAIEX":
        tr = bars_frame(conn, "TAIEX_TR", adjust=False)["close"]
        if tr.empty:
            raise ValueError("沒有加權報酬指數；請先執行 twtm prices backfill --only twse.total_return.TAIEX_TR")
        r = tr.pct_change().reindex(index)
        return r.to_numpy(), "加權股價報酬指數（含息）"
    px = bars_frame(conn, symbol)["close"]
    return px.pct_change().reindex(index).to_numpy(), f"{symbol} 價格指數（不含息）"


def run_hedge_study(conn: sqlite3.Connection, symbol: str, cfg: dict,
                    progress=lambda *_: None) -> HedgeStudy:
    hc = cfg["hedge"]
    bars = bars_frame(conn, symbol).loc[:pd.Timestamp(hc["data_end"])]
    f_all = add_features(bars, cfg)
    lv_all = price_weakness(f_all, cfg)
    start = max(lv_all["level"].first_valid_index(), f_all["ma60"].first_valid_index())
    f, lv = f_all.loc[start:], lv_all.loc[start:]
    p = Prices.from_frame(f)
    s_ret, stock_label = stock_returns(conn, symbol, f.index)
    fs = front_month(load_futures(conn, hc["futures"]), f.index)
    coverage = {"days": len(f), "stock_missing": int(np.isnan(s_ret).sum()),
                "futures_missing": int(np.isnan(fs.overnight).sum() + np.isnan(fs.intraday).sum()),
                "rolls": int(fs.roll_after.sum())}

    split = pd.Timestamp(hc["selection_end"])
    periods = {"full": np.ones(len(f), bool), "selection": f.index < split,
               "test": f.index >= split}
    level = lv["level"].fillna(-1).astype(int).to_numpy()
    down = (f["close"].diff() < 0).to_numpy()
    below_ma = (f["close"] < f["ma60"]).to_numpy()
    ma_tiers = np.where(below_ma, 2, 0)
    cost = hc["cost"]

    def sub(m):
        return s_ret[m], fs.slice(m), p.slice(m)

    unhedged = {k: compute(Run(np.nan_to_num(s_ret[m]), np.nan_to_num(s_ret[m]),
                               np.zeros(m.sum()), np.ones(m.sum()), np.nan_to_num(s_ret[m])),
                           p.index[m]) for k, m in periods.items()}

    combos = [HedgeCombo(r, rule) for r, rule in itertools.product(hc["hedge_ratios"], hc["restore_rules"])]
    results = []
    for ci, combo in enumerate(combos):
        progress(ci + 1, len(combos), combo.id)
        hb = (0.0, 0.0, combo.ratio)
        tiers = tier_path(level, restore_condition(f, combo.restore), hc["restore_days"])
        mets, fixed, mar, spot, rnd = {}, {}, {}, {}, {}
        for name, m in periods.items():
            sr, fss, ps = sub(m)
            t = tiers[m]
            run = simulate_hedge(sr, fss, t, hb, cost)
            mets[name] = compute(run, ps.index)
            avg_h = 1 - mets[name].avg_exposure
            fixed[name] = compute(simulate_hedge(sr, fss, np.full(len(t), 2), (0, 0, avg_h), cost), ps.index)
            mar[name] = compute(simulate_hedge(sr, fss, ma_tiers[m], hb, cost), ps.index)
            spot[name] = compute(simulate_hedge(sr, fss, t, hb, cost, spot=ps), ps.index)
            rnd[name] = {}
            eps = episodes(t)
            for kind, allowed in (("unconditional", np.ones(len(t), bool)), ("down_day", down[m])):
                rng = _rng(hc["seed"], combo.id, name, kind)
                vals = {"calmar": [], "max_dd": [], "cagr": []}
                for _ in range(hc["random_sims"]):
                    rt = relocate(eps, len(t), allowed, rng)
                    if rt is None:
                        continue
                    mm = compute(simulate_hedge(sr, fss, rt, hb, cost), ps.index)
                    vals["calmar"].append(mm.calmar)
                    vals["max_dd"].append(mm.max_dd)
                    vals["cagr"].append(mm.cagr)
                arr = {k: np.asarray(v) for k, v in vals.items()}
                st = mets[name]
                rnd[name][kind] = RandomStats(
                    len(arr["calmar"]), {k: float(np.median(v)) for k, v in arr.items()} if len(arr["calmar"]) else {},
                    {"calmar": float((st.calmar > arr["calmar"]).mean()),
                     "max_dd": float((st.max_dd > arr["max_dd"]).mean()),
                     "cagr": float((st.cagr > arr["cagr"]).mean())} if len(arr["calmar"]) else {})
        results.append(HedgeResult(combo, mets, fixed, mar, spot, rnd))

    selected = max(results, key=lambda r: getattr(r.metrics["selection"], hc["selection_metric"]))
    sc = selected.combo
    hb = (0.0, 0.0, sc.ratio)
    tiers = tier_path(level, restore_condition(f, sc.restore), hc["restore_days"])
    run_sel = simulate_hedge(s_ret, fs, tiers, hb, cost)
    runs = {"strategy": run_sel.net, "full": np.nan_to_num(s_ret),
            "ma_rule": simulate_hedge(s_ret, fs, ma_tiers, hb, cost).net}
    stress = stress_windows(f, runs, hc["stress_drawdown"])
    yr = pd.DataFrame(runs, index=f.index)
    yearly = (1 + yr).groupby(yr.index.year).prod() - 1
    a = 1 - run_sel.exposure
    basis = {name: basis_effect(p.slice(m), fs.slice(m), a[m]) for name, m in periods.items()}
    sens = [(c, compute(simulate_hedge(s_ret, fs, tiers, hb, c), p.index)) for c in hc["cost_sensitivity"]]
    return HedgeStudy(symbol, stock_label, cfg, f, periods, unhedged, results, selected, basis,
                      stress, yearly, sens, coverage)
