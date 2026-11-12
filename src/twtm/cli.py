"""命令列工具。

    twtm futures update                       # 每日更新（最近 10 天）
    twtm futures backfill                     # 期交所 3 年視窗全部回補
    twtm futures backfill --source finmind --start 2018-01-01 --end 2023-12-31
    twtm futures show --days 20               # 顯示外資淨未平倉與非法人多空比（估算）
    twtm futures status                       # 資料涵蓋範圍
    twtm prices backfill                      # 加權、櫃買、台積電、除權息、費半、Nasdaq（2005 起）
    twtm prices update                        # 每日更新
    twtm prices check                         # 資料品質檢查
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, timedelta

from . import db, today_taipei
from .http import Fetcher
from .ingest import futures as ingest
from .ingest import prices as ingest_prices
from .metrics import prices as price_metrics
from .metrics.futures import positions
from .sources import PRODUCTS, taifex


def _date(s: str) -> date:
    return date.fromisoformat(s)


def cmd_update(args, conn) -> int:
    print(ingest.update(conn, Fetcher(), days=args.days))
    return 0


def cmd_backfill(args, conn) -> int:
    today = today_taipei()
    products = tuple(args.product) if args.product else tuple(PRODUCTS)
    if args.source == "taifex":
        start = args.start or taifex.window_start(today)
        res = ingest.backfill_taifex(conn, Fetcher(), start, args.end or today,
                                     products=products, force=args.force)
    else:
        if not args.start:
            print("FinMind 回補需要 --start", file=sys.stderr)
            return 2
        end = args.end or taifex.window_start(today) - timedelta(days=1)
        res = ingest.backfill_finmind(conn, Fetcher(), args.start, end,
                                      products=products, force=args.force)
    print(res)
    return 1 if res.stopped_reason and "額度" in res.stopped_reason else 0


def _fmt_int(v) -> str:
    return "—" if v is None else f"{v:,}"


def cmd_show(args, conn) -> int:
    start = (today_taipei() - timedelta(days=args.days * 2)).isoformat()
    for product in args.product or ["TX", "MTX", "TMF"]:
        rows = positions(conn, product, start=start)[-args.days:]
        print(f"\n== {PRODUCTS[product].name}（{product}）")
        print(f"{'日期':<12}{'全市場OI':>10}{'外資淨OI':>10}{'非法人淨口數':>10}{'非法人多空比':>10}  來源")
        for p in rows:
            ratio = "資料不全" if p.retail_ratio is None else f"{p.retail_ratio:+.2%}"
            print(f"{p.trade_date:<12}{_fmt_int(p.oi_total):>12}{_fmt_int(p.foreign_net):>12}"
                  f"{_fmt_int(p.retail_net):>14}{ratio:>14}  {p.sources}")
    return 0


def cmd_status(args, conn) -> int:
    for table in ("fut_institutional", "fut_daily"):
        print(f"\n== {table}")
        for r in conn.execute(
            f"SELECT product, source, MIN(trade_date) lo, MAX(trade_date) hi, "
            f"COUNT(DISTINCT trade_date) n FROM {table} GROUP BY product, source "
            f"ORDER BY product, source"
        ):
            print(f"  {r['product']:<4} {r['source']:<8} {r['lo']} ~ {r['hi']}  {r['n']} 個交易日")
    print(f"\n期交所可查詢視窗：{taifex.window_start(today_taipei())} 起")
    print("「非法人多空比」＝非三大法人的估算值，不等於散戶帳戶實際多空比。")
    return 0


def cmd_prices_backfill(args, conn) -> int:
    only = set(args.only) if args.only else None
    res = ingest_prices.backfill(conn, Fetcher(), args.start or ingest_prices.HISTORY_START,
                                 args.end, only=only, force=args.force)
    print(res)
    return 1 if res.stopped_reason else 0


def cmd_prices_update(args, conn) -> int:
    res = ingest_prices.update(conn, Fetcher(), days=args.days)
    print(res)
    return 1 if res.stopped_reason else 0


def _sample(xs: list[str], n: int = 5) -> str:
    return ", ".join(xs[:n]) + (f" …（共 {len(xs)}）" if len(xs) > n else "")


def cmd_prices_check(args, conn) -> int:
    ingest_prices.seed_instruments(conn)
    all_ok = True
    for r in conn.execute("SELECT symbol, name, market FROM instrument ORDER BY market, symbol"):
        rep = price_metrics.check(conn, r["symbol"], r["market"],
                                  needs_turnover=r["market"] != "US")
        all_ok &= rep.ok
        flag = "OK " if rep.ok else "NG "
        print(f"{flag}{r['symbol']:<6} {r['name']}：{rep.first} ~ {rep.last}，{rep.days} 日")
        for label, xs in (("缺交易日", rep.missing_dates), ("缺 OHLC", rep.missing_ohlc),
                          ("缺成交值", rep.missing_turnover), ("OHLC 矛盾", rep.ohlc_violations),
                          ("單日變動 >10.5%（非除權息日）", rep.big_moves)):
            if xs:
                print(f"     {label}：{_sample(xs)}")
    return 0 if all_ok else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="twtm")
    ap.add_argument("--db", default=str(db.DEFAULT_DB), help="SQLite 路徑（預設 data/twtm.db）")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="group", required=True)

    fut = sub.add_parser("futures", help="期貨籌碼").add_subparsers(dest="cmd", required=True)

    p = fut.add_parser("update", help="每日更新")
    p.add_argument("--days", type=int, default=10)
    p.set_defaults(func=cmd_update)

    p = fut.add_parser("backfill", help="歷史回補（可中斷續跑）")
    p.add_argument("--source", choices=["taifex", "finmind"], default="taifex")
    p.add_argument("--start", type=_date)
    p.add_argument("--end", type=_date)
    p.add_argument("--product", action="append", choices=list(PRODUCTS))
    p.add_argument("--force", action="store_true", help="忽略已完成紀錄，重新抓取")
    p.set_defaults(func=cmd_backfill)

    p = fut.add_parser("show", help="顯示指標")
    p.add_argument("--days", type=int, default=20)
    p.add_argument("--product", action="append", choices=list(PRODUCTS))
    p.set_defaults(func=cmd_show)

    p = fut.add_parser("status", help="資料涵蓋範圍")
    p.set_defaults(func=cmd_status)

    pr = sub.add_parser("prices", help="價格資料").add_subparsers(dest="cmd", required=True)
    p = pr.add_parser("backfill", help="歷史回補（可中斷續跑）")
    p.add_argument("--start", type=_date)
    p.add_argument("--end", type=_date)
    p.add_argument("--only", action="append",
                   help="限定資料集，例如 twse.index_ohlc.TAIEX（可重複）")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_prices_backfill)
    p = pr.add_parser("update", help="每日更新")
    p.add_argument("--days", type=int, default=35)
    p.set_defaults(func=cmd_prices_update)
    p = pr.add_parser("check", help="資料品質檢查")
    p.set_defaults(func=cmd_prices_check)

    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(asctime)s %(levelname)s %(message)s")
    conn = db.connect(args.db)
    try:
        return args.func(args, conn)
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
