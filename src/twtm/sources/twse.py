"""臺灣證券交易所（rwd JSON 端點）。

- MI_5MINS_HIST：加權指數 OHLC（按月）
- FMTQIK：市場成交股數、金額（按月）
- STOCK_DAY：個股日行情（按月；只提供 2010-01-04 以後）
- TWT49U：除權除息計算結果表（可查整年、全部股票）

WAF：連續請求會回 307 導向安全頁，由 http.Fetcher 的 twse 政策退避重試。
"""

from __future__ import annotations

import json
import re
from datetime import date

from . import ParseError
from ..http import Fetcher

BASE = "https://www.twse.com.tw/rwd/zh"
STOCK_DAY_START = date(2010, 1, 4)

NO_DATA = ("沒有符合條件的資料",)


class OutOfRange(Exception):
    """端點不提供該日期區間（例如 STOCK_DAY 2010 年以前）。"""


def roc_to_iso(s: str) -> str:
    """'115/11/02'、' 94/01/03'、'113年03月18日'、'094年01月01日' → ISO 日期。"""
    nums = re.findall(r"\d+", s)
    if len(nums) != 3:
        raise ParseError(f"無法解析民國日期：{s!r}")
    y, m, d = (int(n) for n in nums)
    return date(y + 1911, m, d).isoformat()


def num(s) -> float | None:
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s)
    s = s.replace(",", "").replace("+", "").strip()
    if s in ("", "--", "---", "-", "X0.00"):
        return None
    return float(s)


def _payload(body: bytes) -> dict | None:
    """回傳 JSON；查無資料回 None；其他非 OK 狀態丟例外。"""
    try:
        j = json.loads(body)
    except json.JSONDecodeError as e:
        raise ParseError("證交所回應不是 JSON（可能是 WAF 安全頁）") from e
    stat = str(j.get("stat", ""))
    if stat.upper() == "OK":
        return j
    if any(k in stat for k in NO_DATA):
        return None
    if "小於" in stat or "大於" in stat:
        raise OutOfRange(stat)
    raise ParseError(f"證交所回應狀態：{stat}")


def _rows(j: dict | None, required: list[str]) -> list[dict]:
    if j is None:
        return []
    fields = [f.strip() for f in j.get("fields", [])]
    missing = [f for f in required if f not in fields]
    if missing:
        raise ParseError(f"缺欄位 {missing}；端點可能改版")
    return [dict(zip(fields, r)) for r in j.get("data", [])]


def parse_index_ohlc(body: bytes, fetched_at: str) -> list[dict]:
    cols = ["日期", "開盤指數", "最高指數", "最低指數", "收盤指數"]
    return [{
        "symbol": "TAIEX", "trade_date": roc_to_iso(r["日期"]),
        "open": num(r["開盤指數"]), "high": num(r["最高指數"]),
        "low": num(r["最低指數"]), "close": num(r["收盤指數"]),
        "source": "twse", "fetched_at": fetched_at,
    } for r in _rows(_payload(body), cols)]


def parse_total_return_index(body: bytes, fetched_at: str) -> list[dict]:
    """發行量加權股價報酬指數（含息，只有收盤）。存為標的 TAIEX_TR。"""
    j = _payload(body)
    if j is None:
        return []
    fields = [f.replace("\u3000", "").replace(" ", "").strip() for f in j.get("fields", [])]
    if fields[:2] != ["日期", "發行量加權股價報酬指數"]:
        raise ParseError(f"報酬指數欄位不符：{fields}")
    return [{"symbol": "TAIEX_TR", "trade_date": roc_to_iso(r[0]), "close": num(r[1]),
             "source": "twse", "fetched_at": fetched_at} for r in j.get("data", [])]


def parse_market_turnover(body: bytes, fetched_at: str) -> list[dict]:
    cols = ["日期", "成交股數", "成交金額"]
    return [{
        "symbol": "TAIEX", "trade_date": roc_to_iso(r["日期"]),
        "volume": int(num(r["成交股數"])), "turnover": num(r["成交金額"]),
        "source": "twse", "fetched_at": fetched_at,
    } for r in _rows(_payload(body), cols)]


def parse_stock_day(body: bytes, symbol: str, fetched_at: str) -> list[dict]:
    cols = ["日期", "成交股數", "成交金額", "開盤價", "最高價", "最低價", "收盤價"]
    out = []
    for r in _rows(_payload(body), cols):
        out.append({
            "symbol": symbol, "trade_date": roc_to_iso(r["日期"]),
            "open": num(r["開盤價"]), "high": num(r["最高價"]),
            "low": num(r["最低價"]), "close": num(r["收盤價"]),
            "volume": int(num(r["成交股數"]) or 0), "turnover": num(r["成交金額"]),
            "source": "twse", "fetched_at": fetched_at,
        })
    return out


ACTIONS = {"息": "dividend", "權": "rights", "權息": "both"}


def parse_ex_rights(body: bytes, fetched_at: str) -> list[dict]:
    cols = ["資料日期", "股票代號", "除權息前收盤價", "除權息參考價", "權值+息值", "權/息"]
    out = []
    for r in _rows(_payload(body), cols):
        action = ACTIONS.get(str(r["權/息"]).strip())
        if action is None:
            raise ParseError(f"未知權/息類型：{r['權/息']!r}")
        out.append({
            "symbol": str(r["股票代號"]).strip(), "ex_date": roc_to_iso(r["資料日期"]),
            "action": action, "prev_close": num(r["除權息前收盤價"]),
            "ref_price": num(r["除權息參考價"]), "value": num(r["權值+息值"]),
            "source": "twse", "fetched_at": fetched_at,
        })
    return out


def _ymd(d: date) -> str:
    return d.strftime("%Y%m%d")


def fetch_index_ohlc(fetcher: Fetcher, month: date) -> bytes:
    return fetcher.request("twse", "GET", f"{BASE}/TAIEX/MI_5MINS_HIST",
                           params={"date": _ymd(month), "response": "json"}).content


def fetch_total_return_index(fetcher: Fetcher, month: date) -> bytes:
    return fetcher.request("twse", "GET", f"{BASE}/TAIEX/MFI94U",
                           params={"date": _ymd(month), "response": "json"}).content


def fetch_market_turnover(fetcher: Fetcher, month: date) -> bytes:
    return fetcher.request("twse", "GET", f"{BASE}/afterTrading/FMTQIK",
                           params={"date": _ymd(month), "response": "json"}).content


def fetch_stock_day(fetcher: Fetcher, symbol: str, month: date) -> bytes:
    return fetcher.request("twse", "GET", f"{BASE}/afterTrading/STOCK_DAY",
                           params={"date": _ymd(month), "stockNo": symbol,
                                   "response": "json"}).content


def fetch_ex_rights(fetcher: Fetcher, start: date, end: date) -> bytes:
    return fetcher.request("twse", "GET", f"{BASE}/exRight/TWT49U",
                           params={"startDate": _ymd(start), "endDate": _ymd(end),
                                   "response": "json"}).content
