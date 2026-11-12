"""跌破均線系列（ma-v1）。每個交易日一列，布林欄位表示當日是否成立。

全部只用 t 日（含）以前的資料；「跌破後 N 日站不回」的訊號日是第 N 天。
"""

from __future__ import annotations

import pandas as pd

from .gaps import Variant


def _cross_below(a: pd.Series, b: pd.Series) -> pd.Series:
    return (a < b) & (a.shift(1) >= b.shift(1))


def ma_events(f: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    c = cfg["ma"]
    e = pd.DataFrame(index=f.index)
    for n in c["windows"]:
        e[f"cross{n}"] = _cross_below(f["close"], f[f"ma{n}"])
        e.loc[f[f"ma{n}"].isna() | f[f"ma{n}"].shift(1).isna(), f"cross{n}"] = False

    k = c["hold_days"]
    below = f["close"] < f["ma20"]
    run = below.copy()
    for i in range(1, k):
        run &= below.shift(i, fill_value=False)
    first_above = f["close"].shift(k) >= f["ma20"].shift(k)
    e["hold20"] = run & first_above                      # 第 k 天確認

    e["ma5x20"] = _cross_below(f["ma5"], f["ma20"]).fillna(False)

    s = c["slope_days"]
    down = f["ma20"] < f["ma20"].shift(s)
    e["ma20_turn"] = down & ~down.shift(1, fill_value=True)  # 由不下彎轉為下彎的第一天

    m = c["down_streak"]
    neg = f["close"].diff() < 0
    streak = neg.copy()
    for i in range(1, m):
        streak &= neg.shift(i, fill_value=False)
    e["down_streak"] = streak & ~neg.shift(m, fill_value=True)  # 第 m 天，且不是更長連跌的延續

    e["uptrend"] = (f["ma20"].shift(1) > f[f"ma{c['uptrend_long']}"].shift(1)).fillna(False)
    e["strength"] = (f["ma20"] - f["close"]) / f["atr_t"]   # 收盤低於 20 日線幾個 ATR
    return e.fillna({col: False for col in e.columns if col != "strength"})


def variants(cfg: dict) -> list[Variant]:
    c = cfg["ma"]
    w = c["windows"]
    k, m = c["hold_days"], c["down_streak"]
    up = lambda e: e["uptrend"].astype(bool)  # noqa: E731
    col = lambda name: (lambda e: e[name].astype(bool))  # noqa: E731
    vs = [Variant(f"M{i + 1}_cross{n}", f"收盤跌破 {n} 日線", col(f"cross{n}"))
          for i, n in enumerate(w)]
    vs += [
        Variant("M4_hold20", f"跌破 20 日線後連續 {k} 日站不回（訊號日為第 {k} 天）", col("hold20")),
        Variant("M5_ma5x20", "5 日線下穿 20 日線", col("ma5x20")),
        Variant("M6_ma20_turn", f"20 日線開始下彎（低於 {c['slope_days']} 日前）", col("ma20_turn")),
        Variant("M7_down_streak", f"連續 {m} 日收低", col("down_streak")),
        Variant("M3u_cross20_up", "多頭中跌破 20 日線（20 日線 > 60 日線）",
                lambda e: e["cross20"].astype(bool) & up(e)),
        Variant("M4u_hold20_up", f"多頭中跌破 20 日線後 {k} 日站不回",
                lambda e: e["hold20"].astype(bool) & up(e)),
        Variant("M5u_ma5x20_up", "多頭中 5 日線下穿 20 日線",
                lambda e: e["ma5x20"].astype(bool) & up(e)),
    ]
    return vs
