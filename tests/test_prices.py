import json
from datetime import date

import pytest

from twtm import db
from twtm.ingest import prices as ingest
from twtm.metrics.prices import check, load_bars
from twtm.sources import ParseError, finmind, tpex, twse, yahoo
from conftest import FakeFetcher, fixture_bytes

T = "2026-11-12T00:00:00+00:00"


# ---------- 解析 ----------

@pytest.mark.parametrize("s,iso", [
    ("115/11/02", "2026-11-02"), (" 94/01/03", "2005-01-03"),
    ("113年03月18日", "2024-03-18"), ("094年01月01日", "2005-01-01"),
])
def test_roc_to_iso(s, iso):
    assert twse.roc_to_iso(s) == iso


def test_twse_index_ohlc():
    rows = twse.parse_index_ohlc(fixture_bytes("twse_taiex_ohlc_202611.json"), T)
    first = rows[0]
    assert first["trade_date"] == "2026-11-02"
    assert (first["open"], first["high"], first["low"], first["close"]) == \
        (45034.53, 45156.17, 44399.10, 44920.14)


def test_twse_turnover():
    rows = twse.parse_market_turnover(fixture_bytes("twse_fmtqik_202611.json"), T)
    assert rows[0]["turnover"] == 799687507911 and rows[0]["volume"] == 10422514115


def test_twse_stock_day():
    rows = twse.parse_stock_day(fixture_bytes("twse_stock_day_2330_202606.json"), "2330", T)
    r = rows[0]
    assert r["trade_date"] == "2026-06-01" and r["close"] == 2355.0
    assert r["volume"] == 60942792 and r["turnover"] == 144105259583


def test_twse_ex_rights_2005_format():
    """2005 年格式多了「權值」「息值」欄，仍以「權值+息值」為準。"""
    rows = twse.parse_ex_rights(fixture_bytes("twse_twt49u_2005.json"), T)
    tsmc = next(r for r in rows if r["symbol"] == "2330")
    assert tsmc == {**tsmc, "ex_date": "2005-06-13", "action": "both",
                    "prev_close": 58.10, "ref_price": 52.88}


def test_twse_stat_handling():
    with pytest.raises(twse.OutOfRange):
        twse.parse_stock_day(json.dumps({"stat": "查詢日期小於99年1月4日，請重新查詢!"}).encode(),
                             "2330", T)
    assert twse.parse_index_ohlc(json.dumps({"stat": "很抱歉，沒有符合條件的資料!"}).encode(), T) == []
    with pytest.raises(ParseError):   # WAF 安全頁
        twse.parse_index_ohlc(b"<html>FOR SECURITY REASONS</html>", T)


def test_tpex_index_and_turnover_units():
    ohlc = tpex.parse_index_ohlc(fixture_bytes("tpex_inx_202611.json"), T)
    assert ohlc[0]["trade_date"] == "2026-11-02" and ohlc[0]["close"] == 369.24
    vol = tpex.parse_trading_index(fixture_bytes("tpex_trading_index_202611.json"), T)
    assert vol[0]["trade_date"] == "2026-11-02"
    assert vol[0]["volume"] == 687_422_000           # 張 → 股
    assert vol[0]["turnover"] == 154_820_241_000     # 仟元 → 元


def test_finmind_stock_price():
    rows = finmind.parse_stock_price(fixture_bytes("finmind_stock_2330.json"), "2330", T)
    ex = next(r for r in rows if r["trade_date"] == "2005-06-13")
    assert ex["open"] == 54.0 and ex["source"] == "finmind"


def test_yahoo_dates_in_exchange_timezone_and_zero_volume():
    rows = yahoo.parse_chart(fixture_bytes("yahoo_sox.json"), "^SOX", T)
    # 時間戳為紐約 09:30（UTC 14:30），日期必須是紐約當地日期
    assert rows[0]["trade_date"] == "2025-11-05"
    assert all(r["volume"] is None for r in rows)    # 費半量全是 0 → 無資料
    assert rows[0]["close"] == 7190.27


# ---------- 回補 ----------

def responder(call):
    url = call["url"]
    if "MI_5MINS_HIST" in url:
        return fixture_bytes("twse_taiex_ohlc_202611.json")
    if "MFI94U" in url:
        return fixture_bytes("twse_tr_index_202611.json")
    if "FMTQIK" in url:
        return fixture_bytes("twse_fmtqik_202611.json")
    if "STOCK_DAY" in url:
        return json.dumps({"stat": "很抱歉，沒有符合條件的資料!"}).encode()
    if "TWT49U" in url:
        return fixture_bytes("twse_twt49u_2005.json")
    if "indexInfo/inx" in url:
        return fixture_bytes("tpex_inx_202611.json")
    if "tradingIndex" in url:
        return fixture_bytes("tpex_trading_index_202611.json")
    if "finance.yahoo" in url:
        return fixture_bytes("yahoo_sox.json")
    if "finmind" in url:
        return fixture_bytes("finmind_stock_2330.json")
    raise AssertionError(url)


TODAY = date(2026, 11, 12)


def test_update_merges_ohlc_and_turnover(conn):
    res = ingest.update(conn, FakeFetcher(responder), days=5, today=TODAY)
    assert res.stopped_reason is None
    b = {x.trade_date: x for x in load_bars(conn, "TAIEX")}["2026-11-02"]
    assert b.close == 44920.14 and b.turnover == 799687507911
    t = {x.trade_date: x for x in load_bars(conn, "TPEX")}["2026-11-02"]
    assert t.close == 369.24 and t.turnover == 154_820_241_000


def test_backfill_respects_dataset_ranges(conn):
    f = FakeFetcher(responder)
    ingest.backfill(conn, f, date(2009, 11, 1), date(2010, 2, 28), today=TODAY)
    stock_day = [c for c in f.calls if "STOCK_DAY" in c["url"]]
    assert {c["date"] for c in stock_day} == {"20100101", "20100201"}   # 證交所 2010 起
    fm = [c for c in f.calls if "finmind" in c["url"]]
    assert [(c["start_date"], c["end_date"]) for c in fm] == [("2009-11-01", "2009-12-31")]


def test_one_failing_source_does_not_stop_others(conn):
    def flaky(call):
        if "finance.yahoo" in call["url"]:
            return b"not json"
        return responder(call)
    res = ingest.update(conn, FakeFetcher(flaky), days=5, today=TODAY)
    assert "yahoo.chart.^SOX" in res.stopped_reason
    assert load_bars(conn, "TAIEX")                  # 其他來源照常寫入


# ---------- 還原價與品質檢查 ----------

def _bar(conn, sym, d, o, h, l, c):
    db.upsert(conn, "price_daily", [{"symbol": sym, "trade_date": d, "open": o, "high": h,
              "low": l, "close": c, "volume": 1, "turnover": 1.0, "source": "twse",
              "fetched_at": T}], ("symbol", "trade_date"))


def test_adjusted_prices_remove_ex_rights_gap(conn):
    for d, o, h, l, c in [("2005-06-09", 58.5, 58.5, 57.3, 58.0),
                          ("2005-06-10", 58.3, 58.5, 57.8, 58.1),
                          ("2005-06-13", 54.0, 54.8, 53.5, 54.5)]:
        _bar(conn, "2330", d, o, h, l, c)
    db.upsert(conn, "corporate_action", twse.parse_ex_rights(
        fixture_bytes("twse_twt49u_2005.json"), T), ("symbol", "ex_date", "action"))

    raw = load_bars(conn, "2330", adjust=False)
    assert raw[2].high < raw[1].low                  # 未還原：看起來像嚴格跳空向下
    adj = load_bars(conn, "2330")
    assert adj[2].ex_date and not adj[1].ex_date
    assert adj[1].factor == pytest.approx(52.88 / 58.10)
    assert adj[2].factor == 1.0
    assert adj[1].close == pytest.approx(58.1 * 52.88 / 58.10)
    assert adj[2].high > adj[1].low                  # 還原後：沒有跳空


def test_quality_check_flags_problems(conn):
    for d in ("2026-11-02", "2026-11-03", "2026-11-04"):
        _bar(conn, "TAIEX", d, 100, 101, 99, 100)
    _bar(conn, "2330", "2026-11-02", 100, 101, 99, 100)
    _bar(conn, "2330", "2026-11-04", 100, 99, 98, 120)   # 高點低於收盤、漲 20%
    rep = check(conn, "2330", "TWSE", needs_turnover=True)
    assert rep.missing_dates == ["2026-11-03"]
    assert rep.ohlc_violations == ["2026-11-04"]
    assert rep.big_moves == ["2026-11-04"]
    assert not rep.ok
    assert check(conn, "TAIEX", "TWSE", needs_turnover=True).ok


def test_tpex_trading_index_old_field_name():
    """2005 年等舊資料的量欄位是「成交股數（仟股）」。"""
    vol = tpex.parse_trading_index(fixture_bytes("tpex_trading_index_200501.json"), T)
    assert vol[0]["trade_date"] == "2005-01-03"
    assert vol[0]["volume"] == 428_236_000
    assert vol[0]["turnover"] == 9_042_274_000


def test_twse_total_return_index():
    rows = twse.parse_total_return_index(fixture_bytes("twse_tr_index_202611.json"), T)
    assert rows[0]["symbol"] == "TAIEX_TR" and rows[0]["trade_date"] == "2026-11-02"
    assert rows[0]["close"] > 0 and "open" not in rows[0]
