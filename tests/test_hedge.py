import numpy as np
import pandas as pd
import pytest

from twtm.backtest.engine import Prices
from twtm.backtest.hedge import basis_effect, front_month, simulate_hedge

D = pd.bdate_range("2024-01-01", periods=6)   # 一～六 = D0..D5


def futures():
    """兩個合約：A 最後交易日 D2，B 持續到 D5。"""
    rows = []
    a = [(100, 101), (101, 102), (102, 103)]                 # (open, close) D0..D2
    b = [(98, 99), (99, 100), (100, 101), (101, 105), (105, 104), (104, 106)]
    for i, (o, c) in enumerate(a):
        rows.append({"trade_date": D[i], "contract": "202401", "open": o, "close": c})
    for i, (o, c) in enumerate(b):
        rows.append({"trade_date": D[i], "contract": "202402", "open": o, "close": c})
    return pd.DataFrame(rows)


def test_front_month_rolls_before_last_trading_day():
    fs = front_month(futures(), D)
    # D0、D1 持有 A；D2（A 的最後交易日）已持有 B → D1 收盤轉倉
    # B 在資料最後一天仍在交易，到期日依規則推算（2024-02-21），D5 仍持有 B
    assert list(fs.contract) == ["202401", "202401", "202402", "202402", "202402", "202402"]
    assert list(fs.roll_after[:3]) == [False, True, False]
    # D2 的隔夜報酬用 B 的 D1 收盤 → D2 開盤
    assert fs.overnight[2] == pytest.approx(100 / 100 - 1)
    assert fs.intraday[2] == pytest.approx(101 / 100 - 1)
    assert fs.overnight[1] == pytest.approx(101 / 101 - 1)   # A：D0 收 101 → D1 開 101


def test_simulate_hedge_pnl_and_roll_cost():
    fs = front_month(futures(), D)
    stock = np.array([0, 0.01, 0.01, 0.01, 0.01, 0.0])
    tiers = np.array([2, 2, 2, 2, 0, 0])                     # D0 收盤決定放空 50%
    run = simulate_hedge(stock, fs, tiers, (0.0, 0.0, 0.5), cost=0.001)
    # D1：開盤建倉（隔夜 0），盤中 A 101→102
    assert run.exposure[1] == 0.5
    exp_d1 = 0.01 - 0.5 * (102 / 101 - 1) - 0.001 * 0.5 - 2 * 0.001 * 0.5   # 建倉成本 + D1 收盤轉倉兩腳
    assert run.net[1] == pytest.approx(exp_d1)
    # D3：隔夜 + 盤中都是 B，避險 50%
    exp_d3 = 0.01 - (0.5 * (101 / 101 - 1) + 0.5 * (105 / 101 - 1))
    assert run.gross[3] == pytest.approx(exp_d3)
    # D5：D4 收盤決定不避險 → D5 開盤平倉，隔夜仍承擔 50%
    assert run.exposure[5] == 1.0


def test_spot_mode_has_no_roll_cost():
    fs = front_month(futures(), D)
    spot = Prices.from_frame(pd.DataFrame({"open": [100.0] * 6, "close": [100.0] * 6}, index=D))
    run = simulate_hedge(np.zeros(6), fs, np.array([2, 2, 2, 0, 0, 0]), (0, 0, 0.5), 0.001, spot=spot)
    assert run.cost.sum() == pytest.approx(0.001 * 0.5 * 2)  # 只有建倉與平倉，沒有轉倉


def test_basis_effect_sign():
    fs = front_month(futures(), D)
    spot = Prices.from_frame(pd.DataFrame({"open": [100.0] * 6, "close": [100.0] * 6}, index=D))
    a = np.array([0, 0, 0, 0.5, 0, 0])
    b = basis_effect(spot, fs, a)
    # D3 期貨 101→105（+3.96%）、現貨 0% → 放空期貨吃虧
    assert b["total"] == pytest.approx(0.5 * (105 / 101 - 1))


def test_live_contracts_use_rule_based_expiry():
    from twtm.backtest.hedge import contract_expiry, third_wednesday
    assert third_wednesday("202611") == pd.Timestamp("2026-11-18")
    assert third_wednesday("202612") == pd.Timestamp("2026-12-16")
    d = pd.Timestamp("2026-11-12")
    fut = pd.DataFrame([{"trade_date": d, "contract": c, "open": 1, "close": 1}
                        for c in ("202612", "202611", "202703")])
    exp = contract_expiry(fut)
    # 都還在交易：依規則推算，近月 202611 最早到期
    assert exp.idxmin() == "202611"
    fs = front_month(fut, pd.DatetimeIndex([d]))
    assert fs.contract[0] == "202611"
