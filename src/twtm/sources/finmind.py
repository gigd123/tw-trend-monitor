"""FinMind（第三方整合來源）。

只用來補期交所 3 年視窗以前的期貨籌碼。官方資料優先（見 db.SOURCE_PRIORITY），
重疊期間會被期交所資料覆蓋。

匿名額度很小；設定環境變數 FINMIND_TOKEN（免費註冊）可提高額度。
"""

from __future__ import annotations

import json
import os
from datetime import date

from . import INVESTORS, PRODUCTS, ParseError
from ..http import Fetcher, RateLimited

URL = "https://api.finmindtrade.com/api/v4/data"

SESSIONS = {"position": "regular", "after_market": "after_hours"}


def _payload(body: bytes) -> list[dict]:
    j = json.loads(body)
    if j.get("status") == 402:
        raise RateLimited(f"FinMind: {j.get('msg')}")
    if j.get("status") != 200:
        raise ParseError(f"FinMind 回應錯誤：{j.get('status')} {j.get('msg')}")
    return j.get("data") or []


def parse_institutional(body: bytes, fetched_at: str) -> list[dict]:
    by_id = {p.finmind_id: p.code for p in PRODUCTS.values()}
    out = []
    for r in _payload(body):
        try:
            product = by_id.get(r["futures_id"])
            if product is None:
                continue
            investor = INVESTORS.get(r["institutional_investors"])
            if investor is None:
                raise ParseError(f"未知身份別：{r['institutional_investors']}")
            out.append({
                "trade_date": r["date"],
                "product": product,
                "investor": investor,
                "long_trade_vol": r["long_deal_volume"],
                "long_trade_val_k": r["long_deal_amount"],
                "short_trade_vol": r["short_deal_volume"],
                "short_trade_val_k": r["short_deal_amount"],
                "long_oi": r["long_open_interest_balance_volume"],
                "long_oi_val_k": r["long_open_interest_balance_amount"],
                "short_oi": r["short_open_interest_balance_volume"],
                "short_oi_val_k": r["short_open_interest_balance_amount"],
                "source": "finmind",
                "fetched_at": fetched_at,
            })
        except KeyError as e:
            raise ParseError(f"FinMind 法人資料缺欄位 {e}") from e
    return out


def parse_daily(body: bytes, fetched_at: str) -> list[dict]:
    by_id = {p.finmind_id: p.code for p in PRODUCTS.values()}
    out = []
    for r in _payload(body):
        try:
            contract = r["contract_date"].strip()
            product = by_id.get(r["futures_id"])
            if product is None or "/" in contract:
                continue
            session = SESSIONS.get(r["trading_session"])
            if session is None:
                raise ParseError(f"未知交易時段：{r['trading_session']}")
            settle = r["settlement_price"] or None  # FinMind 以 0 表示無結算價
            out.append({
                "trade_date": r["date"],
                "product": product,
                "contract": contract,
                "session": session,
                "open": r["open"],
                "high": r["max"],
                "low": r["min"],
                "close": r["close"],
                "settle": settle,
                "volume": r["volume"],
                "oi": r["open_interest"] if session == "regular" else None,
                "source": "finmind",
                "fetched_at": fetched_at,
            })
        except KeyError as e:
            raise ParseError(f"FinMind 行情資料缺欄位 {e}") from e
    return out


DATASETS = {
    "institutional": "TaiwanFuturesInstitutionalInvestors",
    "daily": "TaiwanFuturesDaily",
}


def fetch(fetcher: Fetcher, dataset: str, product: str, start: date, end: date) -> bytes:
    params = {
        "dataset": DATASETS[dataset],
        "data_id": PRODUCTS[product].finmind_id,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
    }
    token = os.environ.get("FINMIND_TOKEN")
    if token:
        params["token"] = token
    return fetcher.request("finmind", "GET", URL, params=params).content


def parse_stock_price(body: bytes, symbol: str, fetched_at: str) -> list[dict]:
    """TaiwanStockPrice：個股日行情（用於證交所 STOCK_DAY 2010 年以前）。"""
    out = []
    for r in _payload(body):
        try:
            if r["stock_id"] != symbol:
                continue
            out.append({
                "symbol": symbol, "trade_date": r["date"],
                "open": r["open"] or None, "high": r["max"] or None,
                "low": r["min"] or None, "close": r["close"] or None,
                "volume": r["Trading_Volume"], "turnover": float(r["Trading_money"]),
                "source": "finmind", "fetched_at": fetched_at,
            })
        except KeyError as e:
            raise ParseError(f"FinMind 個股資料缺欄位 {e}") from e
    return out


def fetch_stock_price(fetcher: Fetcher, symbol: str, start: date, end: date) -> bytes:
    params = {"dataset": "TaiwanStockPrice", "data_id": symbol,
              "start_date": start.isoformat(), "end_date": end.isoformat()}
    token = os.environ.get("FINMIND_TOKEN")
    if token:
        params["token"] = token
    return fetcher.request("finmind", "GET", URL, params=params).content


def parse_total_return_index(body: bytes, fetched_at: str) -> list[dict]:
    """TaiwanStockTotalReturnIndex（加權報酬指數，證交所 MFI94U 的備援）。存為 TAIEX_TR。"""
    return [{"symbol": "TAIEX_TR", "trade_date": r["date"], "close": float(r["price"]),
             "source": "finmind", "fetched_at": fetched_at}
            for r in _payload(body) if r.get("stock_id") == "TAIEX"]


def fetch_total_return_index(fetcher: Fetcher, start: date, end: date) -> bytes:
    params = {"dataset": "TaiwanStockTotalReturnIndex", "data_id": "TAIEX",
              "start_date": start.isoformat(), "end_date": end.isoformat()}
    token = os.environ.get("FINMIND_TOKEN")
    if token:
        params["token"] = token
    return fetcher.request("finmind", "GET", URL, params=params).content
