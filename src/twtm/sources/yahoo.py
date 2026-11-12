"""Yahoo Finance chart API（非官方）。

只用於費半、Nasdaq 等沒有免費官方日 K 的海外指數。介面可能改版或限流，
壞掉時只影響國際同步條件，不影響台股資料。
"""

from __future__ import annotations

import json
from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

from . import ParseError
from ..http import Fetcher

URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"


def parse_chart(body: bytes, symbol: str, fetched_at: str) -> list[dict]:
    try:
        chart = json.loads(body)["chart"]
    except (json.JSONDecodeError, KeyError) as e:
        raise ParseError("Yahoo 回應格式不符") from e
    if chart.get("error"):
        raise ParseError(f"Yahoo 錯誤：{chart['error']}")
    result = (chart.get("result") or [None])[0]
    if not result or not result.get("timestamp"):
        return []
    tz = ZoneInfo(result["meta"]["exchangeTimezoneName"])
    q = result["indicators"]["quote"][0]
    out = []
    for i, ts in enumerate(result["timestamp"]):
        close = q["close"][i]
        if close is None:     # 盤中或缺值
            continue
        vol = q.get("volume", [None] * len(result["timestamp"]))[i]
        r4 = lambda v: None if v is None else round(v, 4)  # noqa: E731
        out.append({
            "symbol": symbol,
            "trade_date": datetime.fromtimestamp(ts, tz).date().isoformat(),
            "open": r4(q["open"][i]), "high": r4(q["high"][i]),
            "low": r4(q["low"][i]), "close": r4(close),
            "volume": vol or None,   # 費半的量全是 0，視為無資料
            "source": "yahoo", "fetched_at": fetched_at,
        })
    return out


def fetch_chart(fetcher: Fetcher, symbol: str, start: date, end: date) -> bytes:
    p1 = int(datetime.combine(start, time(), timezone.utc).timestamp())
    p2 = int(datetime.combine(end, time(23, 59), timezone.utc).timestamp())
    return fetcher.request("yahoo", "GET", URL.format(symbol=symbol),
                           params={"period1": p1, "period2": p2, "interval": "1d"}).content
