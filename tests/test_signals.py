import numpy as np
import pandas as pd
import pytest

from twtm.signals import load_config
from twtm.signals.features import add_features, atr
from twtm.signals.gaps import align_us_to_tw, gap_events, signal_dates, variants
from twtm.signals.research import decluster, forward_metrics, verdict, PeriodStats

CFG = load_config()


def frame(rows, start="2024-01-01"):
    """rows: [(open, high, low, close)]，成交值固定 100。"""
    idx = pd.bdate_range(start, periods=len(rows))
    df = pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx, dtype=float)
    df["volume"] = 1
    df["turnover"] = 100.0
    df["ex_date"] = False
    return df


def uptrend(n=80, start=100.0):
    """平穩上漲（每日 +1），讓最後一天位於高檔。"""
    return [(start + i, start + i + 1.0, start + i - 1.0, start + i + 0.5) for i in range(n)]


def test_atr_constant_range():
    df = frame([(10, 11, 9, 10)] * 30)
    assert atr(df, 20).iloc[-1] == pytest.approx(2.0)


def test_high_zone_and_gap_flags():
    rows = uptrend()
    last_low = rows[-1][2]
    rows.append((last_low - 3, last_low - 2, last_low - 6, last_low - 5))  # 嚴格跳空、收破前低
    f = add_features(frame(rows), CFG)
    t = f.iloc[-1]
    assert t["high_zone"] and t["gap_open"] and t["gap_strict"] and t["close_below_prev_low"]
    assert t["gap_atr"] == pytest.approx(3 / f["atr_prev"].iloc[-1])


def test_features_use_no_future_data():
    rng = np.random.default_rng(0)
    c = 100 + rng.normal(0, 1, 150).cumsum()
    rows = [(x + rng.normal(0, .3), x + abs(rng.normal(0, 1)) + .5,
             x - abs(rng.normal(0, 1)) - .5, x) for x in c]
    full = add_features(frame(rows), CFG)
    cols = ["atr_prev", "range_pos", "high_zone", "gap_open", "gap_strict", "gap_atr",
            "close_below_prev_low", "vol_ratio"]
    for k in (70, 100, 149):
        part = add_features(frame(rows[:k + 1]), CFG)
        pd.testing.assert_series_equal(part[cols].iloc[-1], full[cols].iloc[k], check_names=False)


def _gap_then(after):
    """高檔後一天開盤跳空（不是嚴格跳空），之後接 after 列。"""
    rows = uptrend()
    pl = rows[-1][2]                      # 缺口上緣 = 前日低點
    rows.append((pl - 2, pl - 0.5, pl - 4, pl - 3))
    return rows + [(o + pl, h + pl, l + pl, c + pl) for o, h, l, c in after], pl


def test_unfilled_gap_signal_date_is_confirmation_day():
    rows, pl = _gap_then([(-3, -1, -5, -2)] * 6)          # 之後 6 天最高都 < 缺口上緣
    f = add_features(frame(rows), CFG)
    ev = gap_events(f, CFG)
    gap_day = f.index[80]
    assert ev.loc[gap_day, "unfilled_3_date"] == f.index[83]
    assert ev.loc[gap_day, "unfilled_5_date"] == f.index[85]


def test_gap_filled_intraday_or_later_is_not_unfilled():
    rows, pl = _gap_then([(-3, -1, -5, -2), (-1, +0.5, -2, 0)] + [(-3, -1, -5, -2)] * 4)
    f = add_features(frame(rows), CFG)
    ev = gap_events(f, CFG)
    assert pd.isna(ev.loc[f.index[80], "unfilled_3_date"])   # 第 2 天回補


def test_unfilled_unknown_when_future_missing():
    rows, _ = _gap_then([(-3, -1, -5, -2)])
    f = add_features(frame(rows), CFG)
    ev = gap_events(f, CFG)
    e = ev.loc[f.index[80]]
    assert pd.isna(e["unfilled_3_date"]) and not e["unfilled_3_known"]


def test_ex_date_gaps_are_excluded():
    rows, _ = _gap_then([(-3, -1, -5, -2)] * 3)
    df = frame(rows)
    df.loc[df.index[80], "ex_date"] = True
    assert gap_events(add_features(df, CFG), CFG).empty


def test_us_alignment_uses_previous_us_session():
    tw = pd.DatetimeIndex(["2026-11-11", "2026-11-12"])
    us = pd.Series([True, False, True], index=pd.DatetimeIndex(
        ["2026-11-09", "2026-11-10", "2026-11-11"]), dtype="boolean")
    out = align_us_to_tw(tw, us)
    # 台股 11/11 對應美股 11/10（11/11 美股當天尚未開盤）
    assert out.loc["2026-11-11"] == False  # noqa: E712
    assert out.loc["2026-11-12"] == True   # noqa: E712


def test_us_alignment_stale_is_unknown():
    out = align_us_to_tw(pd.DatetimeIndex(["2026-11-20"]),
                         pd.Series([True], index=pd.DatetimeIndex(["2026-11-02"]), dtype="boolean"))
    assert pd.isna(out.iloc[0])


def test_variant_control_excludes_high_zone():
    rows, _ = _gap_then([(-3, -1, -5, -2)] * 6)
    f = add_features(frame(rows), CFG)
    ev = gap_events(f, CFG)
    vs = {v.id: v for v in variants(CFG)}
    assert len(signal_dates(ev, vs["V0_base"])) == 1
    assert len(signal_dates(ev, vs["V8_control"])) == 0
    assert signal_dates(ev, vs["V4a_unfilled3"])[0] == f.index[83]


def test_forward_metrics():
    df = frame([(10, 11, 9, 10), (10, 12, 8, 11), (11, 13, 7, 12), (12, 14, 10, 13)])
    fwd = forward_metrics(df, [1, 3], mae_days=2)
    assert fwd["r1"].iloc[0] == pytest.approx(0.1)
    assert fwd["r3"].iloc[0] == pytest.approx(0.3)
    assert fwd["mae"].iloc[0] == pytest.approx(7 / 10 - 1)     # 之後 2 日最低 7
    assert pd.isna(fwd["r3"].iloc[1])                           # 未來不足


def test_decluster():
    cal = pd.bdate_range("2024-01-01", periods=100)
    d = cal[[0, 5, 21, 22, 50]]
    assert list(decluster(d, cal, 20)) == list(cal[[0, 21, 50]])


def _ps(n, r5, r20, p=0.05):
    return PeriodStats(n=n, n_dc=n, diff_all={"r5": r5, "r20": r20}, p_all={"r20": p})


@pytest.mark.parametrize("d,v,expected", [
    (_ps(10, -.01, -.02), _ps(30, -.01, -.02), "樣本不足"),
    (_ps(30, -.01, -.02), _ps(30, -.01, -.02), "兩期一致偏弱（顯著）"),
    (_ps(30, -.01, -.02, .3), _ps(30, -.01, -.02), "兩期一致偏弱"),
    (_ps(30, -.01, -.02), _ps(30, .01, -.02), "兩期不一致"),
    (_ps(30, .01, .02), _ps(30, .01, .02), "未偏弱"),
])
def test_verdict_rules(d, v, expected):
    assert verdict(d, v, 15) == expected
