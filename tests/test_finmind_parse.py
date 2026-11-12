import json

import pytest

from twtm.http import RateLimited
from twtm.sources import finmind
from conftest import fixture_bytes

T = "2026-11-12T00:00:00+00:00"


def test_institutional_maps_labels():
    rows = finmind.parse_institutional(fixture_bytes("finmind_inst_mtx.json"), T)
    assert {r["investor"] for r in rows} == {"dealer", "trust", "foreign"}
    assert all(r["product"] == "MTX" and r["source"] == "finmind" for r in rows)
    dealer = next(r for r in rows if r["trade_date"] == "2021-01-04" and r["investor"] == "dealer")
    assert dealer["long_oi"] == 15342 and dealer["short_oi"] == 8265


def test_daily_sessions_and_spreads():
    rows = finmind.parse_daily(fixture_bytes("finmind_daily_mtx.json"), T)
    assert rows and all("/" not in r["contract"] for r in rows)
    after = [r for r in rows if r["session"] == "after_hours"]
    assert after and all(r["oi"] is None and r["settle"] is None for r in after)
    jan = next(r for r in rows if r["contract"] == "202101" and r["session"] == "regular")
    assert jan["oi"] == 38810


def test_rate_limited_payload():
    body = json.dumps({"status": 402, "msg": "Requests reach the upper limit."}).encode()
    with pytest.raises(RateLimited):
        finmind.parse_institutional(body, T)
