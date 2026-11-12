import numpy as np
import pandas as pd
import pytest

from twtm.backtest.engine import Prices, simulate, simulate_fixed, tier_path
from twtm.backtest.metrics import compute, max_drawdown
from twtm.backtest.random_alerts import episodes, relocate
from twtm.risk.levels import price_weakness
from twtm.signals import load_config
from twtm.signals.features import add_features

CFG = load_config()


def prices(opens, closes):
    idx = pd.bdate_range("2024-01-01", periods=len(closes))
    return Prices.from_frame(pd.DataFrame({"open": opens, "close": closes}, index=idx))


# ---------- 減碼層級狀態機 ----------

def test_escalate_immediately_restore_after_n_days():
    lv = np.array([0, 1, 2, 1, 1, 1, 0, 0, 0, 0])
    t = tier_path(lv, None, restore_days=3)
    # 升高立即；降低要連續 3 日低於目前層級
    assert list(t) == [0, 1, 2, 2, 2, 1, 1, 1, 0, 0]


def test_price_restore_condition():
    lv = np.array([0, 2, 0, 0, 0])
    ok = np.array([False, False, False, True, False])
    assert list(tier_path(lv, ok, 3)) == [0, 2, 2, 0, 0]


def test_level3_maps_to_tier2_and_missing_is_zero():
    assert list(tier_path(np.array([-1, 3, 3]), None, 1)) == [0, 2, 2]


# ---------- 報酬計算：隔日開盤執行 ----------

def test_next_open_execution_and_cost():
    # 第 0 日收盤決定減碼到 50%；第 1 日：隔夜仍滿倉，開盤後 50%
    p = prices([100, 110, 100], [100, 100, 100])
    run = simulate(p, np.array([1, 1, 1]), (1.0, 0.5, 0.2), cost=0.01)
    assert run.exposure[1] == 0.5
    # 隔夜 +10%（滿倉）× 盤中 100/110−1（50%）
    expected = (1 + 0.10) * (1 + 0.5 * (100 / 110 - 1)) - 1 - 0.01 * 0.5
    assert run.net[1] == pytest.approx(expected)
    assert run.cost[2] == 0      # 之後持股不變，無成本


def test_fixed_weight():
    p = prices([100, 100, 100], [100, 110, 99])
    run = simulate_fixed(p, 0.7)
    assert run.net[1] == pytest.approx(0.07)
    assert run.net[2] == pytest.approx(0.7 * (99 / 110 - 1))


def test_max_drawdown():
    assert max_drawdown(np.array([0.1, -0.5, 0.2])) == pytest.approx(-0.5)


def test_missed_and_avoided():
    # 減碼段 1：大盤上漲 → 少賺；減碼段 2：大盤下跌 → 避損
    opens = [100, 100, 110, 110, 110, 100, 100]
    closes = [100, 110, 110, 110, 100, 100, 100]
    p = prices(opens, closes)
    tiers = np.array([2, 0, 0, 2, 0, 0, 0])   # t=0 決定 → t=1 減碼；t=3 決定 → t=4 減碼
    run = simulate(p, tiers, (1.0, 0.5, 0.0), cost=0.0)
    m = compute(run, p.index)
    assert m.episodes == 2 and m.episodes_avoided == 1
    assert m.missed_gain == pytest.approx(0.10)
    assert m.avoided_loss == pytest.approx(0.10 / 1.10, rel=1e-6)


# ---------- 隨機警報 ----------

def test_random_relocation_preserves_episodes():
    tiers = np.array([0, 1, 2, 2, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0])
    eps = episodes(tiers)
    assert [list(e) for e in eps] == [[1, 2, 2], [1]]
    rng = np.random.default_rng(0)
    for _ in range(50):
        out = relocate(eps, len(tiers), np.ones(len(tiers), bool), rng)
        assert sorted(map(list, episodes(out))) == sorted(map(list, eps))
        assert out.sum() == tiers.sum()


def test_random_relocation_respects_allowed_starts():
    tiers = np.array([0, 1, 1, 0, 0, 0, 0, 0, 0, 0])
    allowed = np.zeros(10, bool)
    allowed[6] = True
    out = relocate(episodes(tiers), 10, allowed, np.random.default_rng(1))
    assert list(np.flatnonzero(out)) == [6, 7]


# ---------- 價格趨勢轉弱程度 ----------

def test_price_weakness_levels():
    idx = pd.bdate_range("2024-01-01", periods=60)
    up = [100 + i for i in range(40)]
    down = [up[-1] - 3 * i for i in range(1, 21)]
    c = np.array(up + down, dtype=float)
    df = pd.DataFrame({"open": c, "high": c + 1, "low": c - 1, "close": c,
                       "volume": 1, "turnover": 1.0, "ex_date": False}, index=idx)
    lv = price_weakness(add_features(df, CFG), CFG)
    assert lv["level"].iloc[38] == 0                 # 上漲中
    assert lv["level"].iloc[-1] == 3                 # 持續下跌 → 趨勢惡化
    first_weak = lv.index[(lv["level"] > 0).fillna(False).to_numpy()][0]
    assert first_weak > idx[39]
