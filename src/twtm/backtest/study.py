"""價格風險分數的減碼回測研究（bt-v1）。"""

from __future__ import annotations

import itertools
import sqlite3
import zlib
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..risk.levels import price_weakness
from ..signals import ma as ma_signals
from ..signals.features import add_features, bars_frame
from ..signals.research import forward_metrics
from .engine import Prices, restore_condition, simulate, simulate_fixed, tier_path
from .metrics import Metrics, compute
from .random_alerts import episodes, relocate


@dataclass(frozen=True)
class Combo:
    w1: float          # 初步轉弱持股
    w2: float          # 明顯轉弱持股
    restore: str

    @property
    def id(self) -> str:
        return f"{self.w1:.0%}/{self.w2:.0%}/{self.restore}"


@dataclass
class RandomStats:
    n: int
    median: dict[str, float]
    pct_beat: dict[str, float]     # 策略優於隨機的比例（calmar、max_dd、cagr）


@dataclass
class ComboResult:
    combo: Combo
    metrics: dict[str, Metrics]                    # period → 策略
    fixed_avg: dict[str, Metrics]                  # 固定持股＝策略平均持股
    ma_rule: dict[str, Metrics]                    # 跌破 60 日線減碼（同 w2）
    random: dict[str, dict[str, RandomStats]] = field(default_factory=dict)  # period → kind → stats


@dataclass
class Study:
    symbol: str
    cfg: dict
    f: pd.DataFrame
    levels: pd.DataFrame
    periods: dict[str, np.ndarray]                 # period → 布林遮罩
    full: dict[str, Metrics]                       # 100% 持股
    fixed: dict[str, Metrics]                      # 固定 fixed_weight
    results: list[ComboResult]
    selected: ComboResult
    overlap: dict
    stress: pd.DataFrame
    yearly: pd.DataFrame
    cost_sens: list[tuple[float, Metrics]]


PERIOD_LABELS = {"full": "全期", "selection": "選參期", "test": "檢驗期"}


def _rng(seed: int, *keys: str) -> np.random.Generator:
    return np.random.default_rng([seed, *(zlib.crc32(k.encode()) for k in keys)])


def grid(cfg: dict) -> list[Combo]:
    b = cfg["backtest"]
    return [Combo(w1, w2, r) for w1, w2, r in
            itertools.product(b["initial_weights"], b["strong_weights"], b["restore_rules"])
            if w1 >= w2]


def _random(p: Prices, tiers: np.ndarray, weights, cost: float, strat: Metrics,
            allowed: dict[str, np.ndarray], sims: int, seed: int, key: str) -> dict[str, RandomStats]:
    eps = episodes(tiers)
    out = {}
    for kind, mask in allowed.items():
        rng = _rng(seed, key, kind)
        vals = {"calmar": [], "max_dd": [], "cagr": []}
        for _ in range(sims):
            t = relocate(eps, len(tiers), mask, rng)
            if t is None:
                continue
            m = compute(simulate(p, t, weights, cost), p.index)
            vals["calmar"].append(m.calmar)
            vals["max_dd"].append(m.max_dd)
            vals["cagr"].append(m.cagr)
        n = len(vals["calmar"])
        if n == 0:
            out[kind] = RandomStats(0, {}, {})
            continue
        arr = {k: np.asarray(v) for k, v in vals.items()}
        out[kind] = RandomStats(
            n, {k: float(np.median(v)) for k, v in arr.items()},
            {"calmar": float((strat.calmar > arr["calmar"]).mean()),
             "max_dd": float((strat.max_dd > arr["max_dd"]).mean()),   # 回撤較淺
             "cagr": float((strat.cagr > arr["cagr"]).mean())})
    return out


def overlap_analysis(f: pd.DataFrame, cfg: dict) -> dict:
    """M4、M5、M6 事件的重疊程度，以及合併成獨立風險事件後的回檔機率。"""
    e = ma_signals.ma_events(f, cfg)
    sig = {"M4": e["hold20"], "M5": e["ma5x20"], "M6": e["ma20_turn"]}
    pos = {k: np.flatnonzero(v.to_numpy()) for k, v in sig.items()}
    win = 5
    co = {}
    for a, b in itertools.permutations(sig, 2):
        pb = pos[b]
        near = [np.any(np.abs(pb - i) <= win) for i in pos[a]]
        co[(a, b)] = float(np.mean(near)) if near else float("nan")

    # 合併：所有事件日排序，間隔 <= 10 個交易日視為同一段風險事件
    allpos = sorted({(i, k) for k, v in pos.items() for i in v})
    clusters: list[tuple[int, set[str]]] = []
    last = None
    for i, k in allpos:
        if last is not None and i - last <= 10:
            clusters[-1][1].add(k)
        else:
            clusters.append((i, {k}))
        last = i
    rc = cfg["research"]
    fwd = forward_metrics(f, [20], rc["mae_days"], rc["dd_atr"], rc["dd_pct"])
    split = pd.Timestamp(rc["split_date"])
    rows = []
    for i, ks in clusters:
        d = f.index[i]
        rows.append({"date": d, "n_signals": len(ks), "signals": "+".join(sorted(ks)),
                     "dd": fwd["dd"].iloc[i], "period": "探索期" if d < split else "驗證期"})
    cl = pd.DataFrame(rows)
    base = {p: float(fwd["dd"][(fwd.index < split) == (p == "探索期")].mean())
            for p in ("探索期", "驗證期")}
    return {"raw": {k: len(v) for k, v in pos.items()}, "co": co, "clusters": cl, "base": base}


def stress_windows(f: pd.DataFrame, runs: dict[str, np.ndarray], threshold: float) -> pd.DataFrame:
    """加權從高點回落 >= threshold 的區段（高點 → 低點），各策略在同區段的跌幅。"""
    c = f["close"].to_numpy()
    rows, peak_i, t = [], 0, 0
    n = len(c)
    while t < n:
        if c[t] >= c[peak_i]:
            peak_i = t
        elif c[t] / c[peak_i] - 1 <= -threshold:
            trough_i = t
            u = t
            while u < n and c[u] < c[peak_i]:
                if c[u] < c[trough_i]:
                    trough_i = u
                u += 1
            row = {"peak": f.index[peak_i].date(), "trough": f.index[trough_i].date(),
                   "index": c[trough_i] / c[peak_i] - 1,
                   "recovered": f.index[u].date() if u < n else None}
            for name, net in runs.items():
                eq = np.cumprod(1 + net)
                row[name] = eq[trough_i] / eq[peak_i] - 1
            rows.append(row)
            peak_i, t = u, u
            continue
        t += 1
    return pd.DataFrame(rows)


def run_study(conn: sqlite3.Connection, symbol: str, cfg: dict,
              progress=lambda *_: None) -> Study:
    b = cfg["backtest"]
    f_all = add_features(bars_frame(conn, symbol), cfg)
    lv_all = price_weakness(f_all, cfg)
    start = lv_all["level"].first_valid_index()
    start = max(start, f_all["ma60"].first_valid_index())
    f, lv = f_all.loc[start:], lv_all.loc[start:]
    p = Prices.from_frame(f)
    split = pd.Timestamp(b["selection_end"])
    periods = {"full": np.ones(len(f), bool), "selection": (f.index < split),
               "test": (f.index >= split)}
    level = lv["level"].fillna(-1).astype(int).to_numpy()
    down = (f["close"].diff() < 0).to_numpy()
    cost = b["cost"]

    def per(fn):
        return {k: fn(m) for k, m in periods.items()}

    full = per(lambda m: compute(simulate_fixed(p.slice(m), 1.0), p.index[m]))
    fixed = per(lambda m: compute(simulate_fixed(p.slice(m), b["fixed_weight"]), p.index[m]))
    below_ma = (f["close"] < f[f"ma{b['ma_rule_window']}"]).to_numpy()
    ma_tiers = np.where(below_ma, 2, 0)

    results = []
    combos = grid(cfg)
    for ci, combo in enumerate(combos):
        progress(ci + 1, len(combos), combo.id)
        weights = (1.0, combo.w1, combo.w2)
        tiers = tier_path(level, restore_condition(f, combo.restore), b["restore_days"])
        mets, favg, mar, rnd = {}, {}, {}, {}
        for name, m in periods.items():
            ps, ts = p.slice(m), tiers[m]
            mets[name] = compute(simulate(ps, ts, weights, cost), ps.index)
            favg[name] = compute(simulate_fixed(ps, mets[name].avg_exposure), ps.index)
            mar[name] = compute(simulate(ps, ma_tiers[m], weights, cost), ps.index)
            rnd[name] = _random(ps, ts, weights, cost, mets[name],
                                {"unconditional": np.ones(len(ts), bool), "down_day": down[m]},
                                b["random_sims"], b["seed"], f"{combo.id}|{name}")
        results.append(ComboResult(combo, mets, favg, mar, rnd))

    key = b["selection_metric"]
    selected = max(results, key=lambda r: getattr(r.metrics["selection"], key))
    sc = selected.combo
    sel_tiers = tier_path(level, restore_condition(f, sc.restore), b["restore_days"])
    w = (1.0, sc.w1, sc.w2)
    runs = {"strategy": simulate(p, sel_tiers, w, cost).net,
            "full": simulate_fixed(p, 1.0).net,
            "ma_rule": simulate(p, ma_tiers, w, cost).net}
    stress = stress_windows(f, runs, b["stress_drawdown"])
    yr = pd.DataFrame({k: v for k, v in runs.items()}, index=f.index)
    yearly = (1 + yr).groupby(yr.index.year).prod() - 1
    sens = [(c, compute(simulate(p, sel_tiers, w, c), p.index)) for c in b["cost_sensitivity"]]
    return Study(symbol, cfg, f, lv, periods, full, fixed, results, selected,
                 overlap_analysis(f_all, cfg), stress, yearly, sens)
