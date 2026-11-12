"""證券櫃檯買賣中心（www/zh-tw JSON 端點）。

- indexInfo/inx：櫃買指數 OHLC（按月；日期為西元）
- afterTrading/tradingIndex：日成交量值（按月；日期為民國；張數、金額仟元）
"""

from __future__ import annotations

import json
from datetime import date

from . import ParseError
from ..http import Fetcher
from .twse import num, roc_to_iso

BASE = "https://www.tpex.org.tw/www/zh-tw"


def _table(body: bytes, required: list[str]) -> list[dict]:
    try:
        j = json.loads(body)
    except json.JSONDecodeError as e:
        raise ParseError("櫃買中心回應不是 JSON（端點可能改版）") from e
    tables = j.get("tables") or []
    if not tables:
        return []
    t = tables[0]
    fields = [f.strip() for f in t.get("fields", [])]
    if t.get("data") and (missing := [f for f in required if f not in fields]):
        raise ParseError(f"缺欄位 {missing}；端點可能改版")
    return [dict(zip(fields, r)) for r in t.get("data", [])]


def _iso(s: str) -> str:
    s = str(s).strip()
    if len(s.split("/")[0]) == 4:   # 西元 2026/11/02
        return s.replace("/", "-")
    return roc_to_iso(s)            # 民國 115/11/02


def parse_index_ohlc(body: bytes, fetched_at: str) -> list[dict]:
    return [{
        "symbol": "TPEX", "trade_date": _iso(r["日期"]),
        "open": num(r["開市"]), "high": num(r["最高"]),
        "low": num(r["最低"]), "close": num(r["收市"]),
        "source": "tpex", "fetched_at": fetched_at,
    } for r in _table(body, ["日期", "開市", "最高", "最低", "收市"])]


def parse_trading_index(body: bytes, fetched_at: str) -> list[dict]:
    rows = _table(body, ["日期", "成交張數", "金額（仟元）"])
    return [{
        "symbol": "TPEX", "trade_date": _iso(r["日期"]),
        "volume": int(num(r["成交張數"]) * 1000),     # 張 → 股
        "turnover": num(r["金額（仟元）"]) * 1000,     # 仟元 → 元
        "source": "tpex", "fetched_at": fetched_at,
    } for r in rows]


def _month(d: date) -> str:
    return d.strftime("%Y/%m/01")


def fetch_index_ohlc(fetcher: Fetcher, month: date) -> bytes:
    return fetcher.request("tpex", "GET", f"{BASE}/indexInfo/inx",
                           params={"date": _month(month), "response": "json"}).content


def fetch_trading_index(fetcher: Fetcher, month: date) -> bytes:
    return fetcher.request("tpex", "GET", f"{BASE}/afterTrading/tradingIndex",
                           params={"date": _month(month), "response": "json"}).content
