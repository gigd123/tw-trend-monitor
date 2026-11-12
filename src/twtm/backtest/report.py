"""減碼回測報告（Markdown）。"""

from __future__ import annotations

import pandas as pd

from .. import utc_now_iso
from .engine import RESTORE_LABELS
from .metrics import Metrics
from .study import PERIOD_LABELS, ComboResult, Study


def _pct(x, d=1, sign=True):
    if x is None or pd.isna(x):
        return "—"
    return f"{x * 100:+.{d}f}%" if sign else f"{x * 100:.{d}f}%"


def _f(x, d=2):
    return "—" if x is None or pd.isna(x) else f"{x:.{d}f}"


def _row(name: str, m: Metrics, ref: Metrics) -> str:
    return (f"| {name} | {_pct(m.cagr, 2)} | {_pct(m.max_dd)} | {_pct(m.max_dd - ref.max_dd, 1)} "
            f"| {_f(m.calmar)} | {_pct(m.avg_exposure, 0, False)} | {_f(m.switches_per_year, 1)} "
            f"| {_f(m.reductions_per_year, 1)} | {_pct(m.missed_gain, 0, False)} "
            f"| {_pct(m.avoided_loss, 0, False)} | {_pct(m.cost, 1, False)} |")


HEAD = ("| 方案 | 年化報酬 | 最大回撤 | 回撤改善（vs 滿倉） | 報酬/回撤 | 平均持股 | 每年調整次數 "
        "| 每年減碼次數 | 少賺（合計） | 避損（合計） | 交易成本（合計） |\n"
        "|---|---|---|---|---|---|---|---|---|---|---|")


def render(st: Study, name: str) -> str:
    cfg, b = st.cfg, st.cfg["backtest"]
    sel = st.selected
    c = sel.combo
    L: list[str] = []
    w = L.append
    f = st.f
    w(f"# 價格風險分數減碼回測：{name}（{st.symbol}）\n")
    w(f"- 資料：{f.index[0].date()} ~ {f.index[-1].date()}，{len(f)} 個交易日")
    w(f"- 選參期：至 {b['selection_end']} 前；檢驗期：{b['selection_end']} 起（不參與選參）")
    w(f"- 版本：轉弱程度 `{cfg['risk']['version']}`、回測 `{b['version']}`")
    w(f"- 產生時間：{utc_now_iso()}\n")

    w("## 設計\n")
    w("**價格趨勢轉弱程度**（每日收盤判斷，三個狀態合成一個程度，不各自加分）：\n")
    w(f"- A：收盤連續 {cfg['risk']['hold_days']} 日在 20 日線之下；B：5 日線 < 20 日線；"
      f"C：20 日線 < {cfg['risk']['slope_days']} 日前")
    w(f"- 0 正常（都不成立）、1 初步轉弱（1 項）、2 明顯轉弱（2 項以上）、"
      f"3 趨勢惡化（A、B、C 同時成立連續 {cfg['risk']['persist_days']} 日；持股同明顯轉弱）\n")
    w("**減碼規則**：程度升高立即減碼；程度降低要符合恢復條件才加回。t 日收盤決定，t+1 日開盤執行，"
      f"每次調整成本 = 調整比例 × {b['cost']:.1%}。未持股部分報酬為 0。\n")
    w("**恢復條件**：" + "；".join(f"`{k}` {v}" for k, v in RESTORE_LABELS.items()) + "\n")
    w(f"**選參規則（事先固定）**：選參期「年化報酬 / 最大回撤」最高的組合。"
      f"本次選出：初步轉弱 {c.w1:.0%}、明顯轉弱 {c.w2:.0%}、恢復條件 `{c.restore}`。\n")

    for period in ("selection", "test", "full"):
        ref = st.full[period]
        w(f"## 主要比較：{PERIOD_LABELS[period]}\n")
        w(HEAD)
        w(_row("全程 100% 持股", ref, ref))
        w(_row(f"全程固定 {b['fixed_weight']:.0%}", st.fixed[period], ref))
        w(_row(f"全程固定 {sel.metrics[period].avg_exposure:.0%}（＝策略平均持股）", sel.fixed_avg[period], ref))
        w(_row(f"跌破 {b['ma_rule_window']} 日線減碼至 {c.w2:.0%}", sel.ma_rule[period], ref))
        w(_row("**價格訊號減碼（選出的組合）**", sel.metrics[period], ref))
        w("")
        w("**與隨機警報比較**（次數、每段天數、減碼幅度、平均持股、交易成本都相同，只隨機化起始日）：\n")
        w("| 隨機版本 | 模擬次數 | 隨機中位數 報酬/回撤 | 隨機中位數 最大回撤 | 策略優於隨機的比例：報酬/回撤 | 最大回撤 | 年化報酬 |")
        w("|---|---|---|---|---|---|---|")
        for kind, label in (("unconditional", "起始日不限"), ("down_day", "起始日限下跌日")):
            r = sel.random[period].get(kind)
            if not r or not r.n:
                continue
            w(f"| {label} | {r.n} | {_f(r.median['calmar'])} | {_pct(r.median['max_dd'])} "
              f"| {_pct(r.pct_beat['calmar'], 0, False)} | {_pct(r.pct_beat['max_dd'], 0, False)} "
              f"| {_pct(r.pct_beat['cagr'], 0, False)} |")
        w("")

    w("## 全部參數組合\n")
    w("選參期與檢驗期分開列出。「贏固定」：報酬/回撤高於全程固定持有同樣平均持股；"
      "「贏隨機」：報酬/回撤優於隨機警報（起始日不限）的比例。\n")
    w("| 初步/明顯/恢復 | 選參期 年化 | 回撤 | 報酬/回撤 | 平均持股 | 贏固定 | 贏隨機 "
      "| 檢驗期 年化 | 回撤 | 報酬/回撤 | 平均持股 | 贏固定 | 贏隨機 |")
    w("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in st.results:
        cells = []
        for p in ("selection", "test"):
            m, fa = r.metrics[p], r.fixed_avg[p]
            rnd = r.random[p].get("unconditional")
            cells += [_pct(m.cagr, 2), _pct(m.max_dd), _f(m.calmar), _pct(m.avg_exposure, 0, False),
                      "是" if m.calmar > fa.calmar else "否",
                      _pct(rnd.pct_beat["calmar"], 0, False) if rnd and rnd.n else "—"]
        mark = "**" if r is sel else ""
        w(f"| {mark}{r.combo.w1:.0%}/{r.combo.w2:.0%}/{r.combo.restore}{mark} | " + " | ".join(cells) + " |")

    w(f"\n## 大跌段（加權從高點回落 ≥ {b['stress_drawdown']:.0%}）\n")
    w("| 高點 | 低點 | 回到高點 | 大盤跌幅 | 價格訊號減碼 | 跌破 60 日線 |")
    w("|---|---|---|---|---|---|")
    for _, r in st.stress.iterrows():
        w(f"| {r['peak']} | {r['trough']} | {r['recovered'] or '尚未'} | {_pct(r['index'])} "
          f"| {_pct(r['strategy'])} | {_pct(r['ma_rule'])} |")

    w("\n## 年度報酬（選出的組合）\n")
    w("| 年度 | 100% 持股 | 價格訊號減碼 | 差 | 跌破 60 日線 |")
    w("|---|---|---|---|---|")
    for y, r in st.yearly.iterrows():
        w(f"| {y} | {_pct(r['full'])} | {_pct(r['strategy'])} | {_pct(r['strategy'] - r['full'])} "
          f"| {_pct(r['ma_rule'])} |")

    w("\n## 交易成本敏感度（選出的組合，全期）\n")
    w("| 單邊成本 | 年化報酬 | 最大回撤 | 報酬/回撤 |")
    w("|---|---|---|---|")
    w(f"| {b['cost']:.1%}（預設） | {_pct(sel.metrics['full'].cagr, 2)} | {_pct(sel.metrics['full'].max_dd)} "
      f"| {_f(sel.metrics['full'].calmar)} |")
    for cost, m in st.cost_sens:
        w(f"| {cost:.1%} | {_pct(m.cagr, 2)} | {_pct(m.max_dd)} | {_f(m.calmar)} |")

    ov = st.overlap
    w("\n## 訊號重疊與獨立風險事件\n")
    w("研究中的事件型訊號：M4 跌破 20 日線後 3 日站不回、M5 5 日線下穿 20 日線、M6 20 日線開始下彎。\n")
    w("**重疊程度**（列訊號發生後 ±5 個交易日內，欄訊號也發生的比例）：\n")
    w("| | M4 | M5 | M6 |")
    w("|---|---|---|---|")
    for a in ("M4", "M5", "M6"):
        w(f"| {a}（{ov['raw'][a]} 次） | " + " | ".join(
            "—" if a == b_ else _pct(ov["co"][(a, b_)], 0, False) for b_ in ("M4", "M5", "M6")) + " |")
    cl = ov["clusters"]
    w(f"\n**合併成獨立風險事件**（訊號間隔 ≤ 10 個交易日視為同一段）：原始 "
      f"{sum(ov['raw'].values())} 次 → {len(cl)} 段。\n")
    w("| 期間 | 段數 | 20 日內回檔 ≥ 4 ATR 機率 | 基準 | 只含 1 種訊號的段 | 含 2 種以上的段 |")
    w("|---|---|---|---|---|---|")
    for p in ("探索期", "驗證期"):
        sub = cl[(cl["period"] == p) & cl["dd"].notna()]
        one, many = sub[sub["n_signals"] == 1], sub[sub["n_signals"] >= 2]
        w(f"| {p} | {len(sub)} | {_pct(sub['dd'].mean(), 0, False)} | {_pct(ov['base'][p], 0, False)} "
          f"| {_pct(one['dd'].mean(), 0, False)}（{len(one)} 段） | {_pct(many['dd'].mean(), 0, False)}（{len(many)} 段） |")

    w("\n## 注意事項\n")
    w("- **訊號不是在乾淨的資料上挑出來的**：M4、M5、M6 是看過 2005 年至今全部資料後選的，"
      "檢驗期只對「減碼參數」是樣本外，對「訊號選擇」不是。真正未使用的驗證資料是 2026-11-12 以後的資料。")
    w("- 加權指數是價格指數，不含股利（每年約 3–4%）；未持股部分不計利息。兩者都讓「減碼」看起來比實際略差，"
      "但對所有方案的影響方向相同。")
    w("- 「全程固定持股」假設每日免費再平衡。")
    w("- 大盤回測只代表大盤風險訊號的價值，不等於個股組合的實際效果。")
    return "\n".join(L) + "\n"
