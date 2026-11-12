"""期貨籌碼抓取：每日更新與歷史回補。

回補以「日曆區段」切塊（法人按年、行情按月），區段邊界固定，
完成的區段記在 fetch_log，中斷後重跑會跳過。
最近 SETTLE_DAYS 天內的區段不記為完成，因為資料可能還在更新。
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Callable, Iterator

from .. import db, today_taipei, utc_now_iso
from ..http import Fetcher, RateLimited
from ..sources import PRODUCTS, finmind, taifex

log = logging.getLogger(__name__)

SETTLE_DAYS = 7

INST_KEY = ("trade_date", "product", "investor")
DAILY_KEY = ("trade_date", "product", "contract", "session")


@dataclass
class Result:
    chunks_fetched: int = 0
    chunks_skipped: int = 0
    rows_written: int = 0
    stopped_reason: str | None = None

    def __str__(self) -> str:
        s = (f"抓取 {self.chunks_fetched} 段、略過 {self.chunks_skipped} 段、"
             f"寫入 {self.rows_written} 筆")
        return s + (f"；中止：{self.stopped_reason}" if self.stopped_reason else "")


def year_chunks(start: date, end: date) -> Iterator[tuple[date, date]]:
    for y in range(start.year, end.year + 1):
        yield date(y, 1, 1), date(y, 12, 31)


def month_chunks(start: date, end: date) -> Iterator[tuple[date, date]]:
    d = start.replace(day=1)
    while d <= end:
        nxt = (d.replace(day=28) + timedelta(days=4)).replace(day=1)
        yield d, nxt - timedelta(days=1)
        d = nxt


def _store(conn: sqlite3.Connection, source: str, url: str, params: dict, body: bytes,
           parse: Callable[[bytes, str], list[dict]], table: str, key: tuple) -> tuple[int, int]:
    db.save_raw(conn, source, url, params, 200, body)
    rows = parse(body, utc_now_iso())
    return db.upsert(conn, table, rows, key), len(rows)


def _run_chunk(conn, result: Result, *, source: str, dataset: str, key: str,
               chunk: tuple[date, date], query: tuple[date, date], floor: date, force: bool,
               today: date, do_fetch: Callable[[date, date], tuple[str, dict, bytes]],
               parse, table: str, pkey: tuple) -> None:
    c_start, c_end = (d.isoformat() for d in chunk)
    if not force and db.chunk_done(conn, source, dataset, key, c_start, c_end):
        result.chunks_skipped += 1
        return
    q_start, q_end = query
    status, n_rows = "ok", 0
    try:
        url, params, body = do_fetch(q_start, q_end)
        written, n_rows = _store(conn, source, url, params, body, parse, table, pkey)
        result.rows_written += written
        status = "ok" if n_rows else "empty"
    except taifex.OutOfWindow:
        status = "out_of_window"
    result.chunks_fetched += 1
    # 只有「整段都查過」才記為完成。查詢起點只能因為 floor（視窗起點、商品上市日）
    # 而晚於區段起點；使用者指定的起點造成的部分查詢不算完成。
    covers_chunk = q_start <= max(chunk[0], floor) and q_end >= chunk[1]
    if covers_chunk and chunk[1] < today - timedelta(days=SETTLE_DAYS):
        db.log_chunk(conn, source, dataset, key, c_start, c_end, n_rows, status)
    conn.commit()
    log.info("%s %s %s %s~%s：%s，%d 筆", source, dataset, key, q_start, q_end, status, n_rows)


def backfill_taifex(conn: sqlite3.Connection, fetcher: Fetcher, start: date, end: date,
                    *, products=tuple(PRODUCTS), force: bool = False,
                    today: date | None = None,
                    datasets: tuple[str, ...] = ("institutional", "daily")) -> Result:
    """從期交所回補。

    - 三大法人部位：只能查最近 3 年（自動限制在視窗內）。
    - 期貨每日行情：沒有 3 年限制，可從商品上市日起回補（2026-11-13 實測 2005 年可查）。
    """
    today = today or today_taipei()
    lo_inst = max(start, taifex.window_start(today))
    hi = min(end, today)
    result = Result()

    if "institutional" in datasets and lo_inst <= hi:
        for chunk in year_chunks(lo_inst, hi):
            query = (max(chunk[0], lo_inst), min(chunk[1], hi))
            _run_chunk(
                conn, result, source="taifex", dataset="institutional", key="*",
                chunk=chunk, query=query, floor=taifex.window_start(today), force=force,
                today=today,
                do_fetch=lambda s, e: (taifex.INST_URL, {"start": str(s), "end": str(e)},
                                       taifex.fetch_institutional(fetcher, s, e, today)),
                parse=taifex.parse_institutional, table="fut_institutional", pkey=INST_KEY,
            )

    if "daily" in datasets:
        for product in products:
            listed = date.fromisoformat(PRODUCTS[product].listed)
            lo = max(start, listed)
            for chunk in month_chunks(lo, hi):
                query = (max(chunk[0], lo), min(chunk[1], hi))
                _run_chunk(
                    conn, result, source="taifex", dataset="daily", key=product,
                    chunk=chunk, query=query, floor=listed, force=force, today=today,
                    do_fetch=lambda s, e, p=product: (
                        taifex.DAILY_URL, {"product": p, "start": str(s), "end": str(e)},
                        taifex.fetch_daily(fetcher, p, s, e)),
                    parse=taifex.parse_daily, table="fut_daily", pkey=DAILY_KEY,
                )
    if not result.chunks_fetched and not result.chunks_skipped:
        result.stopped_reason = "沒有需要回補的區間"
    return result


def backfill_finmind(conn: sqlite3.Connection, fetcher: Fetcher, start: date, end: date,
                     *, products=tuple(PRODUCTS), force: bool = False,
                     today: date | None = None) -> Result:
    """從 FinMind 回補（用於期交所視窗以前）。額度用盡時中止，已完成區段保留。"""
    today = today or today_taipei()
    hi = min(end, today)
    result = Result()
    try:
        for dataset, parse, table, pkey in (
            ("institutional", finmind.parse_institutional, "fut_institutional", INST_KEY),
            ("daily", finmind.parse_daily, "fut_daily", DAILY_KEY),
        ):
            for product in products:
                listed = date.fromisoformat(PRODUCTS[product].listed)
                lo = max(start, listed)
                for chunk in year_chunks(lo, hi):
                    query = (max(chunk[0], lo), min(chunk[1], hi))
                    _run_chunk(
                        conn, result, source="finmind", dataset=dataset, key=product,
                        chunk=chunk, query=query, floor=listed, force=force, today=today,
                        do_fetch=lambda s, e, d=dataset, p=product: (
                            finmind.URL, {"dataset": d, "product": p, "start": str(s), "end": str(e)},
                            finmind.fetch(fetcher, d, p, s, e)),
                        parse=parse, table=table, pkey=pkey,
                    )
    except RateLimited as e:
        conn.commit()
        result.stopped_reason = f"{e}（稍後重跑會從中斷處繼續；設定 FINMIND_TOKEN 可提高額度）"
    return result


def update(conn: sqlite3.Connection, fetcher: Fetcher, days: int = 10,
           today: date | None = None) -> Result:
    """每日更新：重新抓最近 days 天（涵蓋假日與官方事後修正）。"""
    today = today or today_taipei()
    return backfill_taifex(conn, fetcher, today - timedelta(days=days), today,
                           force=True, today=today)
