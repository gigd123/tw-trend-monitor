"""臺灣期貨交易所。

使用 CSV 下載端點（每日更新與歷史回補共用同一套解析）：
- futContractsDateDown：三大法人各期貨契約。一次可查一年、所有商品。
- futDataDown：期貨每日行情（含未沖銷契約數）。一次最多一個月。

兩者都只能查「今天往前 3 年」內的資料；超出時回 HTML（JS alert「日期時間錯誤」）。
CSV 編碼為 Big5（用 cp950 解碼，涵蓋 Big5 擴充字）。
"""

from __future__ import annotations

import csv
import io
from datetime import date, timedelta

from . import INVESTORS, PRODUCT_BY_NAME, PRODUCTS, ParseError
from ..http import Fetcher

BASE = "https://www.taifex.com.tw/cht/3"
INST_URL = f"{BASE}/futContractsDateDown"
DAILY_URL = f"{BASE}/futDataDown"

# 單次查詢上限（天）
INST_MAX_DAYS = 365
DAILY_MAX_DAYS = 31


class OutOfWindow(Exception):
    """查詢日期超出期交所保留的 3 年視窗。"""


def window_start(today: date) -> date:
    """可查詢的最早日期（實測：2026-11-12 時最早為 2023-11-13）。"""
    try:
        return today.replace(year=today.year - 3) + timedelta(days=1)
    except ValueError:  # 2/29
        return today.replace(year=today.year - 3, day=28) + timedelta(days=1)


def _is_html(text: str) -> bool:
    head = text.lstrip()[:200].lower()
    return head.startswith("<") or "<html" in head or "<!doctype" in head


def is_date_error(body: bytes) -> bool:
    text = body.decode("cp950", errors="replace")
    return _is_html(text) and "alert(" in text


def _decode_csv(body: bytes) -> list[dict[str, str]]:
    text = body.decode("cp950", errors="replace")
    if _is_html(text):
        if "alert(" in text:
            raise OutOfWindow("期交所回應日期錯誤（超出 3 年視窗或區間過長）")
        raise ParseError("期交所回應 HTML，不是 CSV")
    reader = csv.reader(io.StringIO(text))
    try:
        header = [h.strip() for h in next(reader)]
    except StopIteration:
        return []
    rows = []
    for rec in reader:
        if not rec or not rec[0].strip():
            continue
        rows.append({h: (rec[i].strip() if i < len(rec) else "") for i, h in enumerate(header) if h})
    return rows


def _int(s: str) -> int | None:
    s = s.replace(",", "").strip()
    if s in ("", "-", "NULL"):
        return None
    return int(float(s))


def _float(s: str) -> float | None:
    s = s.replace(",", "").strip()
    if s in ("", "-", "NULL"):
        return None
    return float(s)


def _iso(d: str) -> str:
    return d.strip().replace("/", "-")


def parse_institutional(body: bytes, fetched_at: str) -> list[dict]:
    """解析三大法人各期貨契約 CSV，只保留 PRODUCTS 中的商品。"""
    out = []
    for r in _decode_csv(body):
        try:
            product = PRODUCT_BY_NAME.get(r["商品名稱"])
            if product is None:
                continue
            investor = INVESTORS.get(r["身份別"])
            if investor is None:
                raise ParseError(f"未知身份別：{r['身份別']}")
            row = {
                "trade_date": _iso(r["日期"]),
                "product": product,
                "investor": investor,
                "long_trade_vol": _int(r["多方交易口數"]),
                "long_trade_val_k": _int(r["多方交易契約金額(千元)"]),
                "short_trade_vol": _int(r["空方交易口數"]),
                "short_trade_val_k": _int(r["空方交易契約金額(千元)"]),
                "long_oi": _int(r["多方未平倉口數"]),
                "long_oi_val_k": _int(r["多方未平倉契約金額(千元)"]),
                "short_oi": _int(r["空方未平倉口數"]),
                "short_oi_val_k": _int(r["空方未平倉契約金額(千元)"]),
                "source": "taifex",
                "fetched_at": fetched_at,
            }
            net = _int(r["多空未平倉口數淨額"])
        except KeyError as e:
            raise ParseError(f"法人 CSV 缺欄位 {e}；端點可能改版") from e
        if row["long_oi"] is None or row["short_oi"] is None:
            raise ParseError(f"未平倉為空：{r}")
        if net is not None and net != row["long_oi"] - row["short_oi"]:
            raise ParseError(f"淨額與多空不符：{r}")
        out.append(row)
    return out


SESSIONS = {"一般": "regular", "盤後": "after_hours", "": "regular"}


def parse_daily(body: bytes, fetched_at: str) -> list[dict]:
    """解析期貨每日行情 CSV。排除價差組合（到期月份含 '/'）。"""
    out = []
    for r in _decode_csv(body):
        try:
            contract = r["到期月份(週別)"].strip()
            if "/" in contract:
                continue
            product = r["契約"].strip()
            if product not in PRODUCTS:
                continue
            session = SESSIONS.get(r.get("交易時段", "").strip())
            if session is None:
                raise ParseError(f"未知交易時段：{r.get('交易時段')}")
            out.append({
                "trade_date": _iso(r["交易日期"]),
                "product": product,
                "contract": contract,
                "session": session,
                "open": _float(r["開盤價"]),
                "high": _float(r["最高價"]),
                "low": _float(r["最低價"]),
                "close": _float(r["收盤價"]),
                "settle": _float(r["結算價"]),
                "volume": _int(r["成交量"]),
                "oi": _int(r["未沖銷契約數"]) if session == "regular" else None,
                "source": "taifex",
                "fetched_at": fetched_at,
            })
        except KeyError as e:
            raise ParseError(f"行情 CSV 缺欄位 {e}；端點可能改版") from e
    return out


def _fmt(d: date) -> str:
    return d.strftime("%Y/%m/%d")


def fetch_institutional(fetcher: Fetcher, start: date, end: date,
                        today: date | None = None) -> bytes:
    """所有商品的三大法人部位（commodityId 留空＝全部）。

    實測：結束日若是「今天」而今天資料尚未公布（約 15:00 前），端點回日期錯誤；
    行情端點則不會。此時改以昨天為結束日重查。
    """
    def get(e: date) -> bytes:
        return fetcher.request("taifex", "POST", INST_URL, data={
            "queryStartDate": _fmt(start), "queryEndDate": _fmt(e), "commodityId": "",
        }).content

    body = get(end)
    if today is not None and end >= today and is_date_error(body):
        yesterday = today - timedelta(days=1)
        if start > yesterday:
            return b""  # 只查今天且尚未公布
        body = get(yesterday)
    return body


def fetch_daily(fetcher: Fetcher, product: str, start: date, end: date) -> bytes:
    r = fetcher.request("taifex", "POST", DAILY_URL, data={
        "down_type": "1", "commodity_id": PRODUCTS[product].daily_id,
        "queryStartDate": _fmt(start), "queryEndDate": _fmt(end),
    })
    return r.content
