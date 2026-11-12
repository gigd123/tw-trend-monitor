"""高檔跳空向下系列訊號（mvp-spec-v1.2 §4）。

gap_events() 列出每個「開盤跳空向下」日及其屬性；
variants() 依規格 §4.2 的固定清單，產生各變體的訊號日。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np
import pandas as pd


def intl_flags(intl: pd.DataFrame, cfg: dict) -> pd.Series:
    """美股端條件：日報酬 <= -k 倍 ATR%，或收盤 < N 日均線。索引為美股交易日。"""
    k, ma = cfg["intl"]["atr_mult"], cfg["intl"]["ma"]
    weak_day = intl["ret_1d"] <= -k * intl["atr_pct_prev"]
    below_ma = intl["close"] < intl["close"].rolling(ma, min_periods=ma).mean()
    flag = (weak_day | below_ma).astype("boolean")
    flag[intl["atr_pct_prev"].isna()] = pd.NA
    return flag


def align_us_to_tw(tw_dates: pd.DatetimeIndex, us_flag: pd.Series) -> pd.Series:
    """台股 t 日對應「美股交易日 < t」的最後一筆（美股收盤早於台股開盤）。"""
    left = pd.DataFrame({"tw": tw_dates}).sort_values("tw")
    right = pd.DataFrame({"us": us_flag.index, "flag": us_flag.values}).sort_values("us")
    m = pd.merge_asof(left, right, left_on="tw", right_on="us",
                      allow_exact_matches=False, direction="backward")
    # 對齊到的美股資料若超過 5 天前（資料缺漏），視為未知
    stale = (m["tw"] - m["us"]).dt.days > 5
    m.loc[stale, "flag"] = pd.NA
    return pd.Series(m["flag"].values, index=m["tw"].values, dtype="boolean")


def gap_events(f: pd.DataFrame, cfg: dict, peer: pd.DataFrame | None = None,
               intl: pd.DataFrame | None = None) -> pd.DataFrame:
    """每個開盤跳空向下日一列。f / peer / intl 都是 add_features() 的輸出。"""
    g = f[f["gap_open"] & f["atr_prev"].notna() & ~f["ex_date"].astype(bool)].copy()
    idx = {d: i for i, d in enumerate(f.index)}
    highs, dates = f["high"].to_numpy(), f.index

    lo_bin, hi_bin = cfg["gap"]["size_bins"]
    g["size_bin"] = np.select([g["gap_atr"] < lo_bin, g["gap_atr"] < hi_bin],
                              ["small", "mid"], "large")
    if cfg["gap"].get("volume_mode", "ratio") == "percentile":
        vcol, thr = "vol_pct", cfg["gap"]["volume_pct"]
    else:
        vcol, thr = "vol_ratio", cfg["gap"]["volume_ratio"]
    g["vol_up"] = (g[vcol] >= thr).astype("boolean")
    g.loc[g[vcol].isna(), "vol_up"] = pd.NA

    for k in cfg["gap"]["unfilled_days"]:
        sig = []
        for d, prev_low in zip(g.index, g["prev_low"]):
            i = idx[d]
            if i + k >= len(f):
                sig.append(pd.NaT)          # 未來資料不足，尚無法確認
            elif (highs[i:i + k + 1] < prev_low).all():
                sig.append(dates[i + k])    # 第 k 日確認未回補 → 訊號日
            else:
                sig.append(pd.NaT)
        g[f"unfilled_{k}_date"] = sig
        g[f"unfilled_{k}_known"] = [idx[d] + k < len(f) for d in g.index]

    g["strength"] = g["gap_atr"]
    if peer is not None:
        p = peer.reindex(g.index)
        sync = (p["gap_open"] | p["close_below_prev_low"]).astype("boolean")
        sync[p["close"].isna()] = pd.NA
        g["peer_sync"] = sync
    if intl is not None:
        g["intl_sync"] = align_us_to_tw(g.index, intl_flags(intl, cfg)).reindex(g.index)
    return g


@dataclass(frozen=True)
class Variant:
    id: str
    label: str
    select: Callable[[pd.DataFrame], pd.Series]   # 對 gap_events 的列做篩選
    signal_col: str | None = None                 # 訊號日欄位；None = 跳空當日


def variants(cfg: dict) -> list[Variant]:
    hz = lambda g: g["high_zone"].astype(bool)  # noqa: E731
    lo_bin, hi_bin = cfg["gap"]["size_bins"]
    k1, k2 = cfg["gap"]["unfilled_days"]
    t = lambda s: s.fillna(False).astype(bool)  # noqa: E731  未知視為不符合
    return [
        Variant("V0_base", "高檔＋開盤跳空（基礎）", hz),
        Variant("V1_strict", "高檔＋嚴格跳空（最高 < 前日最低）", lambda g: hz(g) & g["gap_strict"]),
        Variant("V2a_small", f"高檔跳空，幅度 < {lo_bin} ATR", lambda g: hz(g) & (g["size_bin"] == "small")),
        Variant("V2b_mid", f"高檔跳空，幅度 {lo_bin}–{hi_bin} ATR", lambda g: hz(g) & (g["size_bin"] == "mid")),
        Variant("V2c_large", f"高檔跳空，幅度 ≥ {hi_bin} ATR", lambda g: hz(g) & (g["size_bin"] == "large")),
        Variant("V3_close_below", "高檔跳空＋收盤跌破前日低點", lambda g: hz(g) & g["close_below_prev_low"]),
        Variant(f"V4a_unfilled{k1}", f"高檔跳空＋缺口 {k1} 日未回補（訊號日 t+{k1}）",
                lambda g: hz(g) & g[f"unfilled_{k1}_date"].notna(), f"unfilled_{k1}_date"),
        Variant(f"V4b_unfilled{k2}", f"高檔跳空＋缺口 {k2} 日未回補（訊號日 t+{k2}）",
                lambda g: hz(g) & g[f"unfilled_{k2}_date"].notna(), f"unfilled_{k2}_date"),
        Variant("V5_volume", "高檔跳空＋量能放大（" + (
            f"量比百分位 ≥ {cfg['gap']['volume_pct']:.0%}"
            if cfg["gap"].get("volume_mode") == "percentile"
            else f"量比 ≥ {cfg['gap']['volume_ratio']}") + "）",
            lambda g: hz(g) & t(g["vol_up"])),
        Variant("V6_peer_sync", "高檔跳空＋同日另一市場同步轉弱", lambda g: hz(g) & t(g.get("peer_sync", pd.Series(False, g.index)))),
        Variant("V7_intl_sync", "高檔跳空＋前一美股交易日費半轉弱", lambda g: hz(g) & t(g.get("intl_sync", pd.Series(False, g.index)))),
        Variant("V8_control", "對照：非高檔的開盤跳空", lambda g: ~hz(g)),
    ]


def signal_dates(events: pd.DataFrame, v: Variant) -> pd.DatetimeIndex:
    sel = events[v.select(events)]
    dates = sel.index if v.signal_col is None else pd.DatetimeIndex(sel[v.signal_col])
    return pd.DatetimeIndex(sorted(dates.dropna().unique()))
