"""價格趨勢轉弱程度（pw-v1）。

由研究中兩期、兩市場都提高回檔機率的三個訊號（M4、M5、M6）改成每日狀態：
  A：收盤連續 hold_days 日在 20 日線之下
  B：5 日線 < 20 日線
  C：20 日線 < slope_days 日前
三者高度相關，不各自加分，而是合成一個程度：
  0 正常：都不成立
  1 初步轉弱：成立 1 項
  2 明顯轉弱：成立 2 項以上
  3 趨勢惡化：A、B、C 同時成立連續 persist_days 日
全部只用當日收盤以前的資料。均線資料不足的日子程度為 NA。
"""

from __future__ import annotations

import pandas as pd

LABELS = {0: "正常", 1: "初步轉弱", 2: "明顯轉弱", 3: "趨勢惡化"}


def _consecutive(cond: pd.Series, n: int) -> pd.Series:
    out = cond.copy()
    for i in range(1, n):
        out &= cond.shift(i, fill_value=False)
    return out


def price_weakness(f: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    c = cfg["risk"]
    out = pd.DataFrame(index=f.index)
    below = (f["close"] < f["ma20"]).fillna(False)
    out["A"] = _consecutive(below, c["hold_days"])
    out["B"] = (f["ma5"] < f["ma20"]).fillna(False)
    out["C"] = (f["ma20"] < f["ma20"].shift(c["slope_days"])).fillna(False)
    count = out[["A", "B", "C"]].sum(axis=1)
    level = count.clip(upper=2).astype("Int64")
    all3 = _consecutive(out["A"] & out["B"] & out["C"], c["persist_days"])
    level[all3] = 3
    warm = f["ma20"].isna() | f["ma20"].shift(c["slope_days"]).isna() | f["ma5"].isna()
    level[warm] = pd.NA
    out["level"] = level
    out["label"] = out["level"].map(lambda v: "—" if pd.isna(v) else LABELS[int(v)])
    return out
