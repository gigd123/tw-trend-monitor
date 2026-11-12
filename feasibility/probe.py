"""資料來源可行性檢測腳本。

每個來源打一次，回報 HTTP 狀態、是否有資料、最新資料日期。
用途：
  1. 定期確認資料來源仍可用（格式、端點是否變動）。
  2. 在不同網路環境（家用網路、GitHub Actions）重跑，
     確認雲端 IP 被擋的來源（國發會、BLS）在該環境是否可用。

執行：python feasibility/probe.py
只需要 requests。證交所有 WAF，請求間隔預設 4 秒，請勿調低。
"""

from __future__ import annotations

import csv
import io
import json
import sys
import time
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Callable

import requests

UA = {"User-Agent": "Mozilla/5.0 (tw-trend-monitor feasibility probe)"}
DELAY_SEC = 4


@dataclass
class Check:
    name: str
    url: str
    parse: Callable[[requests.Response], str]  # 回傳最新日期或摘要；失敗丟例外
    method: str = "GET"
    data: dict | None = None
    headers: dict | None = None  # None → 用預設 UA


# 各來源對 User-Agent 的反應不同（2026-11 實測）：
#   FRED：自訂 UA 會被卡住到逾時，只接受 requests／curl 預設 UA → 傳 {}
#   Yahoo：requests 預設 UA 回 429 → 需要自訂 UA


def _recent_weekday() -> date:
    d = date.today() - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


D = _recent_weekday()
YMD = D.strftime("%Y%m%d")
Y_M_D = D.strftime("%Y/%m/%d")
MONTH_START = D.replace(day=1)


def twse_json(r: requests.Response) -> str:
    j = r.json()
    if j.get("stat", "").upper() != "OK":
        raise ValueError(f"stat={j.get('stat')}")
    return j.get("date", "?")


def tpex_json(r: requests.Response) -> str:
    j = r.json()
    tables = j.get("tables") or []
    if not tables or not tables[0].get("data"):
        raise ValueError("empty tables")
    return tables[0].get("date", j.get("date", "?"))


def openapi_list(date_key: str) -> Callable[[requests.Response], str]:
    def _p(r: requests.Response) -> str:
        rows = r.json()
        if not rows:
            raise ValueError("empty list")
        return max(str(x.get(date_key, "?")) for x in rows)
    return _p


def big5_csv(r: requests.Response) -> str:
    text = r.content.decode("big5", errors="replace")
    rows = list(csv.reader(io.StringIO(text)))
    if len(rows) < 2 or "<" in rows[0][0]:
        raise ValueError("not csv (out of history window?)")
    return rows[1][0] if rows[1] else "?"


def fred_csv(r: requests.Response) -> str:
    lines = [x for x in r.text.splitlines() if x]
    if len(lines) < 2:
        raise ValueError("empty csv")
    return f"{lines[1].split(',')[0]} → {lines[-1]}  ({len(lines) - 1} rows)"


def yahoo(r: requests.Response) -> str:
    res = r.json()["chart"]["result"][0]
    ts = res["timestamp"]
    from datetime import datetime, timezone
    f = lambda t: datetime.fromtimestamp(t, timezone.utc).date()  # noqa: E731
    return f"{f(ts[0])} → {f(ts[-1])}  ({len(ts)} bars)"


def finmind(r: requests.Response) -> str:
    j = r.json()
    if j.get("status") != 200:
        raise ValueError(f"{j.get('status')} {j.get('msg')}")
    d = j.get("data") or []
    if not d:
        raise ValueError("empty data")
    return f"{d[0]['date']} → {d[-1]['date']}"


def http_ok(r: requests.Response) -> str:
    if "Cloudflare" in r.text[:3000] or "Access Denied" in r.text[:3000]:
        raise ValueError("bot protection page")
    return f"{len(r.content)} bytes"


TAIFEX = "https://www.taifex.com.tw/cht/3"
FIN = "https://api.finmindtrade.com/api/v4/data"
YH = "https://query1.finance.yahoo.com/v8/finance/chart/{}?period1=946684800&period2=4102444800&interval=1d"

CHECKS: list[Check] = [
    # --- 證交所 ---
    Check("TWSE 加權指數 OHLC (月)", f"https://www.twse.com.tw/rwd/zh/TAIEX/MI_5MINS_HIST?date={YMD}&response=json", twse_json),
    Check("TWSE 成交量值 (月)", f"https://www.twse.com.tw/rwd/zh/afterTrading/FMTQIK?date={YMD}&response=json", twse_json),
    Check("TWSE 大盤統計/漲跌家數", f"https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date={YMD}&type=MS&response=json", twse_json),
    Check("TWSE 三大法人買賣金額", f"https://www.twse.com.tw/rwd/zh/fund/BFI82U?type=day&dayDate={YMD}&response=json", twse_json),
    Check("TWSE 融資融券統計", f"https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN?date={YMD}&selectType=MS&response=json", twse_json),
    Check("TWSE OpenAPI 全部個股日行情", "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL", openapi_list("Date")),
    # --- 櫃買中心 ---
    Check("TPEx 櫃買指數 OHLC (月)", f"https://www.tpex.org.tw/www/zh-tw/indexInfo/inx?date={Y_M_D}&response=json", tpex_json),
    Check("TPEx 當日彙總/漲跌家數", f"https://www.tpex.org.tw/www/zh-tw/afterTrading/highlight?date={Y_M_D}&response=json", tpex_json),
    Check("TPEx 三大法人彙總", f"https://www.tpex.org.tw/www/zh-tw/insti/summary?date={Y_M_D}&response=json", tpex_json),
    # --- 期交所 ---
    Check("TAIFEX 三大法人各期貨契約 (OpenAPI 當日)", "https://openapi.taifex.com.tw/v1/MarketDataOfMajorInstitutionalTradersDetailsOfFuturesContractsBytheDate", openapi_list("Date")),
    Check("TAIFEX 期貨每日行情含未平倉 (OpenAPI 當日)", "https://openapi.taifex.com.tw/v1/DailyMarketReportFut", openapi_list("Date")),
    Check("TAIFEX P/C Ratio (OpenAPI)", "https://openapi.taifex.com.tw/v1/PutCallRatio", openapi_list("Date")),
    Check("TAIFEX 每日匯率 (OpenAPI)", "https://openapi.taifex.com.tw/v1/DailyForeignExchangeRates", openapi_list("Date")),
    Check("TAIFEX 三大法人 TXF 歷史 CSV（約 3 年視窗）", f"{TAIFEX}/futContractsDateDown", big5_csv, "POST",
          {"queryStartDate": (D - timedelta(days=900)).strftime("%Y/%m/%d"), "queryEndDate": (D - timedelta(days=895)).strftime("%Y/%m/%d"), "commodityId": "TXF"}),
    Check("TAIFEX 期貨行情歷史 CSV (MTX 2010)", f"{TAIFEX}/futDataDown", big5_csv, "POST",
          {"down_type": "1", "commodity_id": "MTX", "queryStartDate": "2010/01/04", "queryEndDate": "2010/01/05"}),
    Check("TAIFEX P/C Ratio 歷史 CSV (2008)", f"{TAIFEX}/pcRatioDown", big5_csv, "POST",
          {"queryStartDate": "2008/01/02", "queryEndDate": "2008/01/04"}),
    Check("TAIFEX 匯率歷史 CSV (2010)", f"{TAIFEX}/dailyFXRateDown", big5_csv, "POST",
          {"queryStartDate": "2010/01/04", "queryEndDate": "2010/01/06"}),
    # --- FRED ---
    *[Check(f"FRED {s}", f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={s}", fred_csv, headers={})
      for s in ["DGS2", "DGS10", "T10Y2Y", "VIXCLS", "DTWEXBGS", "DEXTAUS", "DFEDTARU", "CPIAUCSL", "PAYEMS"]],
    # --- Yahoo Finance（非官方）---
    *[Check(f"Yahoo {s}", YH.format(s), yahoo) for s in ["%5ESOX", "%5EIXIC", "TSM", "DX-Y.NYB", "2330.TW"]],
    # --- FinMind（匿名額度很小）---
    Check("FinMind 整體融資融券 (2005)", f"{FIN}?dataset=TaiwanStockTotalMarginPurchaseShortSale&start_date=2005-01-03&end_date=2005-01-04", finmind),
    Check("FinMind 期貨法人 TX (2020)", f"{FIN}?dataset=TaiwanFuturesInstitutionalInvestors&data_id=TX&start_date=2020-06-01&end_date=2020-06-02", finmind),
    # --- 雲端 IP 常被擋，需在目標執行環境驗證 ---
    Check("國發會 景氣指標", "https://index.ndc.gov.tw/n/zh_tw/data/eco", http_ok),
    Check("BLS CPI 公布行事曆", "https://www.bls.gov/schedule/news_release/cpi.htm", http_ok),
    Check("Fed FOMC 行事曆", "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm", http_ok),
]


def main() -> int:
    print(f"# Probe run {date.today()}  (reference trading day {D})\n")
    print("| 結果 | 來源 | 細節 |\n|---|---|---|")
    failed = 0
    for c in CHECKS:
        # 證交所 WAF 擋下時回 307 導向安全頁；連線也偶爾被重置。退避重試 3 次。
        for attempt in range(3):
            try:
                r = requests.request(c.method, c.url, data=c.data, headers=UA if c.headers is None else c.headers, timeout=60, allow_redirects=False)
                if r.status_code != 200:
                    raise ValueError(f"HTTP {r.status_code}")
                detail, ok = c.parse(r), "OK"
                break
            except Exception as e:  # noqa: BLE001 - probe reports every failure
                detail, ok = f"{type(e).__name__}: {e}"[:120], "FAIL"
                if attempt < 2:
                    time.sleep(10 * (attempt + 1))
        if ok == "FAIL":
            failed += 1
        print(f"| {ok} | {c.name} | {detail} |", flush=True)
        if "twse.com.tw" in c.url or "finmind" in c.url:
            time.sleep(DELAY_SEC)
    print(f"\n{len(CHECKS) - failed}/{len(CHECKS)} OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
