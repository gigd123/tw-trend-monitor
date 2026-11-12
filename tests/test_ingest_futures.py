from datetime import date

import pytest

from twtm import db
from twtm.ingest import futures as ingest
from twtm.metrics.futures import positions
from twtm.sources import taifex
from conftest import FakeFetcher, fixture_bytes

TODAY = date(2026, 11, 12)


def taifex_responder(call):
    if call["url"] == taifex.INST_URL:
        return fixture_bytes("taifex_inst_all_2d.csv.raw")
    return {
        "MTX": fixture_bytes("taifex_daily_mtx.csv.raw"),
        "TMF": fixture_bytes("taifex_daily_tmf.csv.raw"),
    }.get(call["commodity_id"], b"")


def test_update_ingests_and_computes_retail_ratio(conn):
    res = ingest.update(conn, FakeFetcher(taifex_responder), days=3, today=TODAY)
    assert res.rows_written > 0 and res.stopped_reason is None

    mtx = {p.trade_date: p for p in positions(conn, "MTX")}["2026-11-11"]
    assert mtx.complete
    assert mtx.oi_total == 37412
    assert mtx.retail_net == 11544 - 8158
    assert mtx.retail_ratio == pytest.approx(0.0905, abs=5e-5)

    tmf = {p.trade_date: p for p in positions(conn, "TMF")}["2026-11-11"]
    assert tmf.retail_ratio == pytest.approx(-0.0700, abs=5e-5)


def test_foreign_net_oi(conn):
    ingest.update(conn, FakeFetcher(taifex_responder), days=3, today=TODAY)
    tx = {p.trade_date: p for p in positions(conn, "TX")}["2026-11-11"]
    assert tx.foreign_net == 8086 - 112357
    # TX 行情沒有給（responder 回空），散戶多空比必須是 None，不能是 0
    assert tx.oi_total is None and not tx.complete and tx.retail_ratio is None


def test_backfill_clamps_to_window_and_skips_done_chunks(conn):
    f = FakeFetcher(taifex_responder)
    ingest.backfill_taifex(conn, f, date(2020, 1, 1), TODAY, products=("TMF",), today=TODAY)
    inst_calls = [c for c in f.calls if c["url"] == taifex.INST_URL]
    assert inst_calls[0]["queryStartDate"] == "2023/11/13"   # 視窗起點
    daily = [c for c in f.calls if c["url"] == taifex.DAILY_URL]
    assert daily[0]["queryStartDate"] == "2024/07/29"         # TMF 上市日
    assert all(c["commodity_id"] == "TMF" for c in daily)

    # 第二次執行：已完成的區段跳過，只重抓最近（未結算）的區段
    f2 = FakeFetcher(taifex_responder)
    res = ingest.backfill_taifex(conn, f2, date(2020, 1, 1), TODAY, products=("TMF",), today=TODAY)
    assert res.chunks_skipped > 0
    refetched = {(c["url"], c["queryStartDate"]) for c in f2.calls}
    assert refetched == {(taifex.INST_URL, "2026/01/01"), (taifex.DAILY_URL, "2026/11/01")}


def test_partial_chunk_not_marked_done(conn):
    # 只抓 10 月後半段，10 月不能被記成完成
    ingest.backfill_taifex(conn, FakeFetcher(taifex_responder), date(2026, 10, 20),
                           date(2026, 10, 31), products=("MTX",), today=TODAY)
    assert not db.chunk_done(conn, "taifex", "daily", "MTX", "2026-10-01", "2026-10-31")


def test_out_of_window_chunk_is_logged_not_fatal(conn):
    html = fixture_bytes("taifex_out_of_window.html")
    res = ingest.backfill_taifex(conn, FakeFetcher(lambda c: html), date(2024, 1, 1),
                                 date(2024, 1, 31), products=("MTX",), today=TODAY)
    assert res.stopped_reason is None
    row = conn.execute("SELECT status FROM fetch_log WHERE dataset='daily'").fetchone()
    assert row["status"] == "out_of_window"


def test_raw_responses_are_kept(conn):
    ingest.update(conn, FakeFetcher(taifex_responder), days=3, today=TODAY)
    raw_id = conn.execute("SELECT id FROM raw_fetch WHERE url=?", (taifex.INST_URL,)).fetchone()[0]
    assert db.load_raw(conn, raw_id) == fixture_bytes("taifex_inst_all_2d.csv.raw")


def test_institutional_today_unpublished_falls_back_to_yesterday(conn):
    """實測：法人端點結束日為今天且尚未公布時回日期錯誤，需改查到昨天。"""
    html = fixture_bytes("taifex_out_of_window.html")

    def responder(call):
        if call["url"] == taifex.INST_URL:
            if call["queryEndDate"] == "2026/11/12":
                return html
            return fixture_bytes("taifex_inst_all_2d.csv.raw")
        return taifex_responder(call)

    f = FakeFetcher(responder)
    ingest.update(conn, f, days=3, today=TODAY)
    ends = [c["queryEndDate"] for c in f.calls if c["url"] == taifex.INST_URL]
    assert ends == ["2026/11/12", "2026/11/11"]
    mtx = {p.trade_date: p for p in positions(conn, "MTX")}["2026-11-11"]
    assert mtx.complete


def test_daily_quotes_are_not_limited_to_window(conn):
    """期貨每日行情沒有 3 年限制；三大法人部位才有。"""
    f = FakeFetcher(taifex_responder)
    ingest.backfill_taifex(conn, f, date(2020, 1, 1), date(2024, 1, 31), products=("MTX",),
                           today=TODAY)
    inst = [c for c in f.calls if c["url"] == taifex.INST_URL]
    daily = [c for c in f.calls if c["url"] == taifex.DAILY_URL]
    assert inst[0]["queryStartDate"] == "2023/11/13"
    assert daily[0]["queryStartDate"] == "2020/01/01"
