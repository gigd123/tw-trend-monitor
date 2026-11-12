"""命令列工具。

    twtm futures update                       # 每日更新（最近 10 天）
    twtm futures backfill                     # 期交所 3 年視窗全部回補
    twtm futures backfill --source finmind --start 2018-01-01 --end 2023-12-31
    twtm futures show --days 20               # 顯示外資淨未平倉與非法人多空比（估算）
    twtm futures status                       # 資料涵蓋範圍
    twtm prices backfill                      # 加權、櫃買、台積電、除權息、費半、Nasdaq（2005 起）
    twtm prices update                        # 每日更新
    twtm prices check                         # 資料品質檢查
    twtm research run --family ma             # 訊號研究（gap | ma | breakout）→ reports/<版本>/
    twtm signals compute                      # 計算訊號並寫入 signal_event
    twtm signals recent                       # 最近的訊號
    twtm risk show                            # 價格趨勢轉弱程度
    twtm backtest run                         # 減碼回測 → reports/<版本>/
    twtm forward record | show                # 前向紀錄
    twtm daily                                # 每日流程（排程用）
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
        datasets = tuple(args.dataset) if args.dataset else ("institutional", "daily")
        res = ingest.backfill_taifex(conn, Fetcher(), start, args.end or today,
                                     products=products, force=args.force, datasets=datasets)
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


def cmd_research(args, conn) -> int:
    from pathlib import Path
    from .signals import load_config
    from .signals.pipeline import PEERS, TITLES, build_family
    from .signals.report import render
    from .signals.research import run

    cfg = load_config(args.config)
    family = getattr(args, "family", None) or "gap"
    symbols = args.symbol or ["TAIEX", "TPEX"]
    for sym in symbols:
        name = conn.execute("SELECT name FROM instrument WHERE symbol=?", (sym,)).fetchone()
        data = build_family(conn, sym, family, cfg, end=cfg["research"].get("data_end"))
        results = run(data.f, data.events, data.variants, cfg, data.alt_mask)
        md = render(sym, name["name"] if name else sym, PEERS.get(sym, "—"), data, results,
                    cfg, TITLES[family])
        out = args.out if args.out and len(symbols) == 1 else \
            str(Path(args.out_dir) / data.version / f"{sym.lower()}.md")
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(md, encoding="utf-8")
        print(f"已寫入 {out}")
    return 0


def cmd_signals_compute(args, conn) -> int:
    from .signals import load_config
    from .signals.pipeline import FAMILIES, build_family
    from .signals.store import save_events

    cfg = load_config(args.config)
    for sym in args.symbol or ["TAIEX", "TPEX"]:
        for fam in FAMILIES:
            d = build_family(conn, sym, fam, cfg)
            n = save_events(conn, "market", sym, d.events, d.variants, d.version)
            print(f"{sym} {fam}：寫入 {n} 筆訊號事件（版本 {d.version}）")
    return 0


def cmd_signals_recent(args, conn) -> int:
    rows = conn.execute(
        "SELECT symbol, signal_date, anchor_date, signal_id, strength, rule_version "
        "FROM signal_event WHERE signal_date >= date('now', ?) "
        "ORDER BY signal_date DESC, symbol, signal_id", (f"-{args.days} days",)).fetchall()
    if not rows:
        print(f"最近 {args.days} 天沒有訊號")
    for r in rows:
        anchor = "" if r["anchor_date"] == r["signal_date"] else f"（跳空日 {r['anchor_date']}）"
        strength = "" if r["strength"] is None else f"強度 {r['strength']:.2f} ATR"
        print(f"{r['signal_date']}  {r['symbol']:<6} {r['signal_id']:<18} "
              f"{strength}{anchor}  [{r['rule_version']}]")
    return 0


def cmd_backtest(args, conn) -> int:
    from pathlib import Path
    from .backtest.report import render
    from .backtest.study import run_study
    from .signals import load_config

    cfg = load_config(args.config)
    if args.sims is not None:
        cfg["backtest"]["random_sims"] = args.sims
    scenarios = cfg["backtest"].get("cost_scenarios") or [None]
    for sym in args.symbol or ["TAIEX"]:
        name = conn.execute("SELECT name FROM instrument WHERE symbol=?", (sym,)).fetchone()
        for cost in scenarios:
            st = run_study(conn, sym, cfg, cost=cost, progress=lambda i, n, cid: print(
                f"  {sym} {i}/{n} {cid}", flush=True) if args.verbose else None)
            suffix = "" if cost is None else f"-cost{cost * 100:.2f}"
            out = Path(args.out_dir) / cfg["backtest"]["version"] / f"{sym.lower()}{suffix}.md"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(render(st, name["name"] if name else sym), encoding="utf-8")
            print(f"已寫入 {out}", flush=True)
    return 0


def cmd_risk_show(args, conn) -> int:
    from .risk.levels import price_weakness
    from .signals import load_config
    from .signals.pipeline import features

    cfg = load_config(args.config)
    for sym in args.symbol or ["TAIEX", "TPEX"]:
        lv = price_weakness(features(conn, sym, cfg), cfg).tail(args.days)
        print(f"\n== {sym} 價格趨勢轉弱程度（{cfg['risk']['version']}）")
        print("日期         程度        A 站不回  B 5<20  C 20 下彎")
        yn = lambda x: "是" if x else "　"  # noqa: E731
        for d, r in lv.iloc[::-1].iterrows():
            print(f"{d.date()}  {r['label']:<8}  {yn(r['A']):^8}  {yn(r['B']):^6}  {yn(r['C']):^8}")
    return 0


def cmd_forward_record(args, conn) -> int:
    from .risk.forward import record
    added = record(conn)
    print("\n".join(added) if added else "沒有新的交易日需要記錄")
    return 0


def cmd_forward_show(args, conn) -> int:
    import json
    from .risk.forward import recent
    rows = recent(conn, args.days)
    if not rows:
        print("尚無前向紀錄；執行 twtm forward record")
        return 0
    print("日期        標的    轉弱程度   bt-v2 選定組合的下一日持股  補記")
    for r in rows:
        tiers = json.loads(r["tiers"])
        v2 = {k.split("|", 1)[1]: v for k, v in tiers.items() if k.startswith("bt-v2|")}
        reduced = sum(1 for v in v2.values() if v >= 2)
        print(f"{r['trade_date']}  {r['symbol']:<6}  {r['label']:<8}  "
              f"{reduced}/{len(v2)} 組處於減碼  {'是' if r['late'] else ''}")
    return 0


def cmd_daily(args, conn) -> int:
    """每日流程：期貨 → 價格 → 訊號 → 前向紀錄。單一步驟失敗不中止其他步驟。"""
    from .ingest import prices as ingest_prices
    from .risk.forward import record
    failures = []
    steps = [
        ("期貨籌碼", lambda: print(ingest.update(conn, Fetcher()))),
        ("價格", lambda: print(ingest_prices.update(conn, Fetcher()))),
        ("訊號", lambda: cmd_signals_compute(argparse.Namespace(symbol=None, config=None), conn)),
        ("前向紀錄", lambda: print("\n".join(record(conn)) or "沒有新的交易日需要記錄")),
        ("轉弱程度", lambda: cmd_risk_show(argparse.Namespace(symbol=None, days=3, config=None), conn)),
    ]
    for name, fn in steps:
        print(f"\n### {name}", flush=True)
        try:
            fn()
        except Exception as e:  # noqa: BLE001 - 每日流程要盡量跑完
            failures.append(f"{name}：{type(e).__name__}: {e}")
            print(f"失敗：{failures[-1]}", file=sys.stderr)
    if failures:
        print("\n有步驟失敗：\n" + "\n".join(failures), file=sys.stderr)
    return 1 if failures else 0


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
    p.add_argument("--dataset", action="append", choices=["institutional", "daily"],
                   help="限定資料集（期貨每日行情可回補到上市日；法人部位只有 3 年）")
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

    rs = sub.add_parser("research", help="訊號研究").add_subparsers(dest="cmd", required=True)
    for cmd, fam, hlp in (("run", None, "訊號研究（--family 指定系列）"),
                          ("gaps", "gap", "高檔跳空向下（等同 run --family gap）")):
        p = rs.add_parser(cmd, help=hlp)
        if fam is None:
            p.add_argument("--family", choices=["gap", "ma", "breakout"], required=True)
        else:
            p.set_defaults(family=fam)
        p.add_argument("--symbol", action="append", choices=["TAIEX", "TPEX", "2330"])
        p.add_argument("--config", help="參數檔（預設 src/twtm/signals/default.toml）")
        p.add_argument("--out", help="輸出路徑（只指定一個 --symbol 時）")
        p.add_argument("--out-dir", default="reports", help="輸出目錄；檔名為 <版本>/<標的>.md")
        p.set_defaults(func=cmd_research)

    sg = sub.add_parser("signals", help="訊號").add_subparsers(dest="cmd", required=True)
    p = sg.add_parser("compute", help="計算訊號並寫入資料庫")
    p.add_argument("--symbol", action="append", choices=["TAIEX", "TPEX", "2330"])
    p.add_argument("--config")
    p.set_defaults(func=cmd_signals_compute)
    p = sg.add_parser("recent", help="最近的訊號")
    p.add_argument("--days", type=int, default=30)
    p.set_defaults(func=cmd_signals_recent)

    bt = sub.add_parser("backtest", help="減碼回測").add_subparsers(dest="cmd", required=True)
    p = bt.add_parser("run", help="價格風險分數減碼回測 → reports/<版本>/<標的>.md")
    p.add_argument("--symbol", action="append", choices=["TAIEX", "TPEX"])
    p.add_argument("--config")
    p.add_argument("--sims", type=int, help="隨機警報模擬次數（預設依設定檔）")
    p.add_argument("--out-dir", default="reports")
    p.set_defaults(func=cmd_backtest)

    rk = sub.add_parser("risk", help="風險分數").add_subparsers(dest="cmd", required=True)
    p = rk.add_parser("show", help="價格趨勢轉弱程度")
    p.add_argument("--symbol", action="append", choices=["TAIEX", "TPEX", "2330"])
    p.add_argument("--days", type=int, default=15)
    p.add_argument("--config")
    p.set_defaults(func=cmd_risk_show)

    fw = sub.add_parser("forward", help="前向紀錄").add_subparsers(dest="cmd", required=True)
    p = fw.add_parser("record", help="記錄最新交易日（只新增、不覆寫）")
    p.set_defaults(func=cmd_forward_record)
    p = fw.add_parser("show", help="最近的前向紀錄")
    p.add_argument("--days", type=int, default=20)
    p.set_defaults(func=cmd_forward_show)

    p = sub.add_parser("daily", help="每日流程（期貨 → 價格 → 訊號 → 前向紀錄），給排程使用")
    p.set_defaults(func=cmd_daily)

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
