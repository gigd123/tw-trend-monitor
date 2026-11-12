from datetime import date

import pytest

from twtm.sources import ParseError, taifex
from conftest import fixture_bytes

T = "2026-11-12T00:00:00+00:00"


def test_institutional_keeps_only_tracked_products():
    rows = taifex.parse_institutional(fixture_bytes("taifex_inst_all_2d.csv.raw"), T)
    # 2 天 × 3 商品 × 3 身分別
    assert len(rows) == 18
    assert {r["product"] for r in rows} == {"TX", "MTX", "TMF"}
    assert {r["investor"] for r in rows} == {"dealer", "trust", "foreign"}
    assert {r["trade_date"] for r in rows} == {"2026-11-10", "2026-11-11"}


def test_institutional_values_match_openapi():
    """與 2026-11-11 期交所 OpenAPI 回應的數值一致。"""
    rows = taifex.parse_institutional(fixture_bytes("taifex_inst_all_2d.csv.raw"), T)
    fx = next(r for r in rows if r["trade_date"] == "2026-11-11"
              and r["product"] == "TX" and r["investor"] == "foreign")
    assert fx["long_oi"] == 8086
    assert fx["short_oi"] == 112357
    assert fx["short_oi_val_k"] == 991831532
    assert fx["source"] == "taifex"


def test_daily_excludes_spreads_and_nulls_after_hours_oi():
    rows = taifex.parse_daily(fixture_bytes("taifex_daily_mtx.csv.raw"), T)
    assert rows and all("/" not in r["contract"] for r in rows)
    assert {r["session"] for r in rows} == {"regular", "after_hours"}
    assert all(r["oi"] is None for r in rows if r["session"] == "after_hours")
    # 週契約要保留
    assert any("W" in r["contract"] for r in rows)


@pytest.mark.parametrize("fixture,expected", [
    ("taifex_daily_mtx.csv.raw", 37412),
    ("taifex_daily_tmf.csv.raw", 98136),
])
def test_daily_regular_oi_total_matches_openapi(fixture, expected):
    rows = taifex.parse_daily(fixture_bytes(fixture), T)
    assert sum(r["oi"] for r in rows if r["session"] == "regular") == expected


def test_out_of_window_html_raises():
    with pytest.raises(taifex.OutOfWindow):
        taifex.parse_institutional(fixture_bytes("taifex_out_of_window.html"), T)


def test_unexpected_html_raises_parse_error():
    with pytest.raises(ParseError):
        taifex.parse_daily(b"<html><body>maintenance</body></html>", T)


def test_missing_column_raises_parse_error():
    body = "日期,商品名稱,身份別\n2026/11/11,臺股期貨,外資及陸資\n".encode("cp950")
    with pytest.raises(ParseError, match="缺欄位"):
        taifex.parse_institutional(body, T)


def test_net_mismatch_raises():
    raw = fixture_bytes("taifex_inst_all_2d.csv.raw").decode("cp950")
    header, first, *rest = raw.splitlines()
    while "臺股期貨" not in first:
        first, *rest = rest
    cols = first.split(",")
    cols[13] = str(int(cols[13]) + 1)  # 竄改「多空未平倉口數淨額」
    body = "\n".join([header, ",".join(cols)]).encode("cp950")
    with pytest.raises(ParseError, match="淨額"):
        taifex.parse_institutional(body, T)


def test_window_start():
    assert taifex.window_start(date(2026, 11, 12)) == date(2023, 11, 13)
    assert taifex.window_start(date(2028, 2, 29)) == date(2025, 3, 1)
