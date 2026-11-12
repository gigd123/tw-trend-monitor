"""期貨避險回測報告（Markdown）。"""

from __future__ import annotations

from .. import utc_now_iso
from .engine import RESTORE_LABELS
from .hedge_study import PERIOD_LABELS, HedgeStudy
from .metrics import Metrics
from .report import _f, _pct

HEAD = ("| 方案 | 年化報酬 | 最大回撤 | 回撤改善（vs 不避險） | 報酬/回撤 | 平均淨曝險 | 每年調整次數 "
        "| 少賺（合計） | 避損（合計） | 成本（合計） |\n|---|---|---|---|---|---|---|---|---|---|")


def _row(name: str, m: Metrics, ref: Metrics) -> str:
    return (f"| {name} | {_pct(m.cagr, 2)} | {_pct(m.max_dd)} | {_pct(m.max_dd - ref.max_dd)} "
            f"| {_f(m.calmar)} | {_pct(m.avg_exposure, 0, False)} | {_f(m.switches_per_year, 1)} "
            f"| {_pct(m.missed_gain, 0, False)} | {_pct(m.avoided_loss, 0, False)} | {_pct(m.cost, 1, False)} |")


def render(st: HedgeStudy, name: str) -> str:
    hc = st.cfg["hedge"]
    sel = st.selected
    c = sel.combo
    L: list[str] = []
    w = L.append
    f = st.f
    w(f"# 期貨避險回測：{name}（{st.symbol}）\n")
    w(f"- 資料：{f.index[0].date()} ~ {f.index[-1].date()}，{len(f)} 個交易日（研究資料截止 {hc['data_end']}）")
    w(f"- 選參期：至 {hc['selection_end']} 前；檢驗期：{hc['selection_end']} 起")
    w(f"- 版本：轉弱程度 `{st.cfg['risk']['version']}`、避險 `{hc['version']}`；避險工具：{hc['futures']} 近月")
    w(f"- 股票部位報酬：{st.stock_label}")
    w(f"- 產生時間：{utc_now_iso()}\n")
    if st.symbol != "TAIEX":
        w("> **交叉避險**：以台指期避險櫃買部位，會有避險誤差。股票部位用價格指數（不含息），"
          "而台指期的逆價差包含加權成分股的股利，這個偏差**對避險不利**。\n")

    w("## 設計\n")
    w(f"- 股票部位一直 100%；價格趨勢轉弱程度達「明顯轉弱」以上時，放空 {hc['futures']} 近月合約，"
      "名目 = 組合市值 × 避險比例；其他時候不避險。")
    w("- 期貨用一般時段實際成交價；最後交易日的前一日收盤轉倉。正逆價差與轉倉價差直接反映在損益中。")
    w(f"- t 日收盤決定，t+1 日開盤以期貨開盤價調整；成本：單邊 {hc['cost']:.2%}（名目），轉倉算兩腳。")
    w("- 恢復條件：" + "；".join(f"`{k}` {v}" for k, v in RESTORE_LABELS.items()))
    w(f"- 選參規則（事先固定）：選參期報酬/回撤最高。**本次選出：避險比例 {c.ratio:.0%}、恢復條件 `{c.restore}`。**")
    cov = st.coverage
    w(f"- 資料完整性：股票報酬缺 {cov['stock_missing']} 日、期貨報酬缺 {cov['futures_missing']} 筆（缺值以 0 計）、"
      f"期間轉倉 {cov['rolls']} 次\n")

    m, fx = sel.metrics["test"], sel.fixed["test"]
    rnd = sel.random["test"].get("unconditional")
    beat_fixed = m.calmar > fx.calmar
    beat_rand = bool(rnd and rnd.n and rnd.pct_beat["calmar"] >= 0.90)
    yn = lambda x: "✅ 符合" if x else "❌ 不符合"  # noqa: E731
    w("## 事先登記的成功標準（檢驗期）\n")
    w("| 標準 | 策略 | 對照 | 結果 |")
    w("|---|---|---|---|")
    w(f"| 報酬/回撤 高於全程固定避險（{1 - m.avg_exposure:.0%}） | {_f(m.calmar)} | {_f(fx.calmar)} | {yn(beat_fixed)} |")
    w(f"| 報酬/回撤 優於 ≥ 90% 隨機避險（起始日不限） | 優於 {_pct(rnd.pct_beat['calmar'], 0, False) if rnd and rnd.n else '—'} "
      f"| 門檻 90% | {yn(beat_rand)} |")
    w(f"\n**整體：{'通過' if beat_fixed and beat_rand else '未通過'}**\n")

    for period in ("selection", "test", "full"):
        ref = st.unhedged[period]
        w(f"## 主要比較：{PERIOD_LABELS[period]}\n")
        w(HEAD)
        w(_row("不避險（100% 股票）", ref, ref))
        w(_row(f"全程固定避險 {1 - sel.metrics[period].avg_exposure:.0%}（＝策略平均）", sel.fixed[period], ref))
        w(_row(f"跌破 60 日線才避險 {c.ratio:.0%}", sel.ma_rule[period], ref))
        w(_row(f"對照：放空現貨指數（無價差、無轉倉）", sel.spot[period], ref))
        w(_row("**價格訊號期貨避險（選出的組合）**", sel.metrics[period], ref))
        b = st.basis[period]
        w(f"\n正逆價差與轉倉的影響（選出的組合）：避險 {b['hedged_days']} 日、轉倉 {b['rolls']} 次；"
          f"放空期貨相對放空現貨價格指數累計 {_pct(-b['total'])}（每年 {_pct(-b['per_year'], 2)}）；"
          f"其中 6–9 月 {_pct(-b['season'])}、其他月份 {_pct(-b['other'])}。負值＝期貨避險較吃虧。\n")
        w("**與隨機避險比較**（次數、每段天數、避險比例都相同，只隨機化起始日）：\n")
        w("| 隨機版本 | 模擬次數 | 隨機中位數 報酬/回撤 | 隨機中位數 最大回撤 | 策略優於隨機：報酬/回撤 | 最大回撤 | 年化報酬 |")
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
    w("| 避險比例/恢復 | 選參期 年化 | 回撤 | 報酬/回撤 | 贏固定 | 贏隨機 | 檢驗期 年化 | 回撤 | 報酬/回撤 | 贏固定 | 贏隨機 |")
    w("|---|---|---|---|---|---|---|---|---|---|---|")
    for r in st.results:
        cells = []
        for p in ("selection", "test"):
            mm, fx = r.metrics[p], r.fixed[p]
            rr = r.random[p].get("unconditional")
            cells += [_pct(mm.cagr, 2), _pct(mm.max_dd), _f(mm.calmar), "是" if mm.calmar > fx.calmar else "否",
                      _pct(rr.pct_beat["calmar"], 0, False) if rr and rr.n else "—"]
        mark = "**" if r is sel else ""
        w(f"| {mark}{r.combo.id}{mark} | " + " | ".join(cells) + " |")

    w(f"\n## 大跌段（指數從高點回落 ≥ {hc['stress_drawdown']:.0%}）\n")
    w("| 高點 | 低點 | 回到高點 | 指數（價格）跌幅 | 不避險（股票部位） | 價格訊號期貨避險 | 跌破 60 日線 |")
    w("|---|---|---|---|---|---|---|")
    for _, r in st.stress.iterrows():
        w(f"| {r['peak']} | {r['trough']} | {r['recovered'] or '尚未'} | {_pct(r['index'])} "
          f"| {_pct(r['full'])} | {_pct(r['strategy'])} | {_pct(r['ma_rule'])} |")

    w("\n## 年度報酬（選出的組合）\n")
    w("| 年度 | 不避險 | 期貨避險 | 差 | 跌破 60 日線 |")
    w("|---|---|---|---|---|")
    for y, r in st.yearly.iterrows():
        w(f"| {y} | {_pct(r['full'])} | {_pct(r['strategy'])} | {_pct(r['strategy'] - r['full'])} | {_pct(r['ma_rule'])} |")

    w("\n## 成本敏感度（選出的組合，全期）\n")
    w("| 單邊成本 | 年化報酬 | 最大回撤 | 報酬/回撤 |")
    w("|---|---|---|---|")
    mf = sel.metrics["full"]
    w(f"| {hc['cost']:.2%}（預設） | {_pct(mf.cagr, 2)} | {_pct(mf.max_dd)} | {_f(mf.calmar)} |")
    for cost, mm in st.cost_sens:
        w(f"| {cost:.2%} | {_pct(mm.cagr, 2)} | {_pct(mm.max_dd)} | {_f(mm.calmar)} |")

    w("\n## 注意事項\n")
    w("- 檢驗期對「只在明顯轉弱才動作」這個設計不是完全乾淨的（來自 bt-v1 的全期分析）；乾淨的驗證是前向紀錄。")
    w("- 避險部位假設每日調整口數維持固定比例；實際口數是整數（小台、微台可以降低誤差）。")
    w("- 不計保證金的資金成本與現金利息；放空期貨需要準備保證金與追繳緩衝。")
    w("- 大盤回測不等於個股組合；個股組合的避險比例需要依 beta 調整。")
    return "\n".join(L) + "\n"
