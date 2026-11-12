"""研究結果輸出為 Markdown。"""

from __future__ import annotations

import pandas as pd

from .. import utc_now_iso
from .gaps import signal_dates
from .research import VariantResult, forward_metrics


def _pct(x: float | None, digits: int = 2) -> str:
    return "—" if x is None or pd.isna(x) else f"{x * 100:+.{digits}f}%"


def _share(x: float | None) -> str:
    return "—" if x is None or pd.isna(x) else f"{x * 100:.0f}%"


def _pp(x: float | None) -> str:
    """百分點差。"""
    return "—" if x is None or pd.isna(x) else f"{x * 100:+.0f} 點"


def _p(x: float | None) -> str:
    return "—" if x is None or pd.isna(x) else f"{x:.2f}"


def _yn(x) -> str:
    return "—" if x is None or pd.isna(x) else ("是" if x else "否")


def render(symbol: str, name: str, peer: str, data, results: list[VariantResult],
           cfg: dict, title: str) -> str:
    rc = cfg["research"]
    hs = rc["horizons"]
    metric = rc.get("verdict_metric", "returns")
    f = data.f
    split = rc["split_date"]
    split_ts = pd.Timestamp(split)
    fwd = forward_metrics(f, hs, rc["mae_days"], rc.get("dd_atr", 4.0), rc.get("dd_pct", 0.05))
    dd_atr, dd_pct = rc.get("dd_atr", 4.0), rc.get("dd_pct", 0.05)
    L: list[str] = []
    w = L.append

    w(f"# {title}訊號研究：{name}（{symbol}）\n")
    w(f"- 資料：{f.index[0].date()} ~ {f.index[-1].date()}，{len(f)} 個交易日；"
      f"探索期至 {split} 前，驗證期 {split} 起")
    w(f"- 訊號版本：`{data.version}`；研究方法版本：`{rc.get('version', 'research-v1')}`")
    w(f"- 產生時間：{utc_now_iso()}")
    if data.family == "gap":
        w(f"- 同步市場：{peer}；國際：{cfg['intl']['symbol']}（前一美股交易日）")
    w("")

    w("## 怎麼讀\n")
    w("- 這些訊號是**警訊**，不是預測下跌。研究要回答的是：訊號之後，市場出現較大回檔的機率是否比平常高。")
    w(f"- **回檔機率（主要）**：訊號日收盤後 {rc['mae_days']} 個交易日內，最低價曾比訊號日收盤低 "
      f"≥ {dd_atr:g} 個 ATR（ATR 以 {cfg['common']['atr_n']} 日計，含訊號日）。")
    w(f"- **5% 回檔機率（輔助）**：同期間內最大跌幅 ≥ {dd_pct:.0%}。")
    w("- **報酬**：訊號日收盤到之後第 N 個交易日收盤。")
    w(f"- **去叢集**：一個訊號後 {rc['decluster_days']} 個交易日內的訊號不重複計入；所有統計都用去叢集後的樣本。")
    w("- **差**：訊號減「同期間所有交易日」。回檔機率差以百分點表示。")
    if metric == "drawdown":
        w(f"- **p**：從同期間所有交易日隨機抽同樣天數（{rc['bootstrap']:,} 次），回檔機率**大於等於**訊號的比例。越小越不像巧合。")
        w("- **判定規則（看結果前固定）**：")
        w(f"  - 任一期去叢集後少於 {rc['min_events']} 次 → 樣本不足")
        w("  - 探索期與驗證期的回檔機率都高於基準 → 兩期回檔機率皆較高；若兩期 p 都 < 0.10 → 加註顯著")
        w("  - 只有一期較高 → 兩期不一致；都不高於基準 → 未提高\n")
    else:
        w(f"- **p**：從同期間所有交易日隨機抽同樣天數（{rc['bootstrap']:,} 次），平均報酬比訊號還低的比例。")
        w("- **判定規則（看結果前固定）**：")
        w(f"  - 任一期去叢集後少於 {rc['min_events']} 次 → 樣本不足")
        w("  - 探索期與驗證期的 5 日、20 日「差」都為負 → 兩期一致偏弱；若兩期 20 日 p 都 < 0.10 → 加註顯著")
        w("  - 只有一期為負 → 兩期不一致；都不為負 → 未偏弱\n")

    # ---- 基準
    w("## 基準（所有交易日）\n")
    w("| 期間 | " + " | ".join(f"{h} 日報酬" for h in hs)
      + f" | 回檔 ≥ {dd_atr:g} ATR 機率 | 回檔 ≥ {dd_pct:.0%} 機率 | 平均 MAE |")
    w("|---|" + "---|" * (len(hs) + 3))
    for label, mask in (("探索期", fwd.index < split_ts), ("驗證期", fwd.index >= split_ts)):
        sub = fwd[mask]
        w(f"| {label} | " + " | ".join(_pct(sub[f'r{h}'].mean()) for h in hs)
          + f" | {_share(sub['dd'].mean())} | {_share(sub['dd5'].mean())} | {_pct(sub['mae'].mean())} |")

    # ---- 摘要
    w("\n## 摘要\n")
    if metric == "drawdown":
        w(f"| 變體 | 判定 | 次數 探索/驗證 | 回檔 ≥ {dd_atr:g} ATR 機率 探索/驗證 | 較基準 探索/驗證 "
          f"| p 探索/驗證 | 20 日報酬差 探索/驗證 |")
        w("|---|---|---|---|---|---|---|")
        for r in results:
            d, v = r.periods["discovery"], r.periods["validation"]
            w(f"| {r.variant.id} {r.variant.label} | {r.verdict} | {d.n_dc}/{v.n_dc} "
              f"| {_share(d.mean.get('dd'))} / {_share(v.mean.get('dd'))} "
              f"| {_pp(d.diff_all.get('dd'))} / {_pp(v.diff_all.get('dd'))} "
              f"| {_p(d.p_all.get('dd'))} / {_p(v.p_all.get('dd'))} "
              f"| {_pct(d.diff_all.get('r20'))} / {_pct(v.diff_all.get('r20'))} |")
    else:
        w("| 變體 | 判定 | 次數 探索/驗證 | 5 日差 探索/驗證 | 20 日差 探索/驗證 | 20 日 p 探索/驗證 | 20 日內 MAE（全期） |")
        w("|---|---|---|---|---|---|---|")
        for r in results:
            d, v, a = r.periods["discovery"], r.periods["validation"], r.periods["all"]
            w(f"| {r.variant.id} {r.variant.label} | {r.verdict} | {d.n_dc}/{v.n_dc} "
              f"| {_pct(d.diff_all.get('r5'))} / {_pct(v.diff_all.get('r5'))} "
              f"| {_pct(d.diff_all.get('r20'))} / {_pct(v.diff_all.get('r20'))} "
              f"| {_p(d.p_all.get('r20'))} / {_p(v.p_all.get('r20'))} "
              f"| {_pct(a.mean.get('mae'))} |")

    # ---- 明細
    alt_col = "dd" if metric == "drawdown" else "r20"
    alt_head = (f"回檔機率差 vs {data.alt_label}" if metric == "drawdown"
                else f"20 日差 vs {data.alt_label}") if data.alt_label else "—"
    w("\n## 各變體明細（全期，去叢集後）\n")
    w("| 變體 | 原始/去叢集 | " + " | ".join(f"{h} 日報酬" for h in hs)
      + f" | 回檔 ≥ {dd_atr:g} ATR | 回檔 ≥ {dd_pct:.0%} | 20 日下跌比例 | {alt_head} |")
    w("|---|---|" + "---|" * (len(hs) + 4))
    for r in results:
        a = r.periods["all"]
        alt_v = a.diff_alt.get(alt_col)
        w(f"| {r.variant.id} | {a.n}/{a.n_dc} | "
          + " | ".join(_pct(a.mean.get(f'r{h}')) for h in hs)
          + f" | {_share(a.mean.get('dd'))} | {_share(a.mean.get('dd5'))}"
          f" | {_share(a.pct_down.get('r20'))}"
          f" | {_pp(alt_v) if metric == 'drawdown' else _pct(alt_v)} |")

    # ---- 最近事件
    if data.family == "gap":
        L.extend(_recent_gaps(data.events, fwd, cfg))
    else:
        L.extend(_recent_signals(data, fwd, cfg))

    w("\n## 注意事項\n")
    w("- 這是事件研究，還沒有計入減碼、恢復與交易成本（Phase 5）。")
    w("- 同一系列的變體高度重疊，不是獨立的檢定；多個變體同時看起來有效時，不代表證據加倍。"
      "比較的組數越多，越可能有幾組「碰巧」看起來有效。")
    w("- 報酬與回檔都從訊號日收盤起算；實際執行最快是隔日開盤。")
    if data.family == "gap":
        w("- 國際同步使用 Yahoo 費半資料（非官方）。")
    return "\n".join(L) + "\n"


def _recent_gaps(events: pd.DataFrame, fwd: pd.DataFrame, cfg: dict) -> list[str]:
    k1 = cfg["gap"]["unfilled_days"][0]
    vmode = cfg["gap"].get("volume_mode", "ratio")
    vcol, vlabel = ("vol_pct", "量比百分位") if vmode == "percentile" else ("vol_ratio", "量比")
    out = ["\n## 最近 15 次高檔開盤跳空\n",
           f"| 日期 | 跳空 ATR | 跳空 % | 嚴格跳空 | 收盤破前低 | {vlabel} | 同步市場 | 費半轉弱 "
           f"| {k1} 日未補 | 之後 5 日 | 之後 20 日 | 20 日內大回檔 |",
           "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    hz = events[events["high_zone"].astype(bool)].tail(15)
    for d, e in hz.iloc[::-1].iterrows():
        unf = "未知" if not e[f"unfilled_{k1}_known"] else _yn(pd.notna(e[f"unfilled_{k1}_date"]))
        vv = e.get(vcol)
        vtxt = "—" if pd.isna(vv) else (f"{vv:.0%}" if vmode == "percentile" else f"{vv:.2f}")
        dd = fwd.loc[d, "dd"]
        out.append(
            f"| {d.date()} | {e['gap_atr']:.2f} | {e['gap_pct'] * 100:.2f}% | {_yn(e['gap_strict'])} "
            f"| {_yn(e['close_below_prev_low'])} | {vtxt} | {_yn(e.get('peer_sync'))} "
            f"| {_yn(e.get('intl_sync'))} | {unf} | {_pct(fwd.loc[d, 'r5'])} "
            f"| {_pct(fwd.loc[d, 'r20'])} | {'未知' if pd.isna(dd) else _yn(bool(dd))} |")
    return out


def _recent_signals(data, fwd: pd.DataFrame, cfg: dict) -> list[str]:
    rows: dict[pd.Timestamp, list[str]] = {}
    for v in data.variants:
        for d in signal_dates(data.events, v)[-40:]:
            rows.setdefault(d, []).append(v.id.split("_")[0])
    out = ["\n## 最近 15 個訊號日\n",
           "| 日期 | 成立的變體 | 強度（ATR） | 之後 5 日 | 之後 20 日 | 20 日內大回檔 |",
           "|---|---|---|---|---|---|"]
    for d in sorted(rows)[-15:][::-1]:
        s = data.events.loc[d, "strength"] if "strength" in data.events else None
        dd = fwd.loc[d, "dd"]
        out.append(f"| {d.date()} | {', '.join(rows[d])} | {'—' if pd.isna(s) else f'{s:.2f}'} "
                   f"| {_pct(fwd.loc[d, 'r5'])} | {_pct(fwd.loc[d, 'r20'])} "
                   f"| {'未知' if pd.isna(dd) else _yn(bool(dd))} |")
    return out
