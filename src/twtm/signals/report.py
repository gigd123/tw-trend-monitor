"""研究結果輸出為 Markdown。"""

from __future__ import annotations

import pandas as pd

from .. import utc_now_iso
from .research import VariantResult, forward_metrics


def _pct(x: float | None, digits: int = 2) -> str:
    return "—" if x is None or pd.isna(x) else f"{x * 100:+.{digits}f}%"


def _share(x: float | None) -> str:
    return "—" if x is None or pd.isna(x) else f"{x * 100:.0f}%"


def _p(x: float | None) -> str:
    return "—" if x is None or pd.isna(x) else f"{x:.2f}"


def render(symbol: str, name: str, peer: str, f: pd.DataFrame, events: pd.DataFrame,
           results: list[VariantResult], cfg: dict) -> str:
    rc = cfg["research"]
    hs = rc["horizons"]
    split = rc["split_date"]
    first, last = f.index[0].date(), f.index[-1].date()
    fwd = forward_metrics(f, hs, rc["mae_days"])
    L = []
    w = L.append

    w(f"# 高檔跳空向下訊號研究：{name}（{symbol}）\n")
    w(f"- 資料：{first} ~ {last}，{len(f)} 個交易日；探索期至 {split} 前，驗證期 {split} 起")
    w(f"- 參數版本：`{cfg['version']}`（`src/twtm/signals/default.toml`）")
    w(f"- 產生時間：{utc_now_iso()}")
    w(f"- 同步市場：{peer}；國際：{cfg['intl']['symbol']}（前一美股交易日）\n")

    w("## 怎麼讀\n")
    w("- **報酬**：訊號日收盤到之後第 N 個交易日收盤。**MAE**：之後 20 日內最低價相對訊號日收盤的最大跌幅。")
    w(f"- **去叢集**：一個訊號後 {rc['decluster_days']} 個交易日內的訊號不重複計入；所有平均都用去叢集後的樣本。")
    w("- **差**：訊號平均減「同期間所有交易日」平均。負值＝訊號後表現比一般日子差。")
    w(f"- **p**：從同期間所有交易日隨機抽同樣天數（{rc['bootstrap']:,} 次），平均報酬比訊號還低的比例。越小越不像巧合。")
    w("- **判定規則（看結果前固定）**：")
    w(f"  - 任一期去叢集後少於 {rc['min_events']} 次 → 樣本不足")
    w("  - 探索期與驗證期的 5 日、20 日「差」都為負 → 兩期一致偏弱；若兩期 20 日 p 都 < 0.10 → 加註顯著")
    w("  - 只有一期為負 → 兩期不一致；都不為負 → 未偏弱\n")

    w("## 摘要\n")
    w("| 變體 | 判定 | 次數 探索/驗證（去叢集） | 5 日差 探索/驗證 | 20 日差 探索/驗證 | 20 日 p 探索/驗證 | 20 日內 MAE（全期） |")
    w("|---|---|---|---|---|---|---|")
    for r in results:
        d, v, a = r.periods["discovery"], r.periods["validation"], r.periods["all"]
        w(f"| {r.variant.id} {r.variant.label} | {r.verdict} | {d.n_dc}/{v.n_dc} "
          f"| {_pct(d.diff_all.get('r5'))} / {_pct(v.diff_all.get('r5'))} "
          f"| {_pct(d.diff_all.get('r20'))} / {_pct(v.diff_all.get('r20'))} "
          f"| {_p(d.p_all.get('r20'))} / {_p(v.p_all.get('r20'))} "
          f"| {_pct(a.mean.get('mae'))} |")

    w("\n## 基準（所有交易日平均）\n")
    w("| 期間 | " + " | ".join(f"{h} 日" for h in hs) + " | MAE |")
    w("|---|" + "---|" * (len(hs) + 1))
    split_ts = pd.Timestamp(split)
    for label, mask in (("探索期", fwd.index < split_ts), ("驗證期", fwd.index >= split_ts)):
        sub = fwd[mask]
        w(f"| {label} | " + " | ".join(_pct(sub[f'r{h}'].mean()) for h in hs)
          + f" | {_pct(sub['mae'].mean())} |")

    w("\n## 各變體明細（全期，去叢集後）\n")
    w("| 變體 | 原始/去叢集 | " + " | ".join(f"{h} 日平均" for h in hs)
      + " | 5 日下跌比例 | 20 日下跌比例 | 20 日差 vs 高檔未跳空 |")
    w("|---|---|" + "---|" * (len(hs) + 3))
    for r in results:
        a = r.periods["all"]
        w(f"| {r.variant.id} | {a.n}/{a.n_dc} | "
          + " | ".join(_pct(a.mean.get(f'r{h}')) for h in hs)
          + f" | {_share(a.pct_down.get('r5'))} | {_share(a.pct_down.get('r20'))}"
          f" | {_pct(a.diff_hz.get('r20'))} |")

    w("\n## 最近 15 次高檔開盤跳空\n")
    w("| 日期 | 跳空 ATR | 跳空 % | 嚴格跳空 | 收盤破前低 | 量比 | 同步市場 | 費半轉弱 | 3 日未補 | 之後 5 日 | 之後 20 日 |")
    w("|---|---|---|---|---|---|---|---|---|---|---|")
    yn = lambda x: "—" if pd.isna(x) else ("是" if x else "否")  # noqa: E731
    hz = events[events["high_zone"].astype(bool)].tail(15)
    k1 = cfg["gap"]["unfilled_days"][0]
    for d, e in hz.iloc[::-1].iterrows():
        unf = "未知" if not e[f"unfilled_{k1}_known"] else yn(pd.notna(e[f"unfilled_{k1}_date"]))
        w(f"| {d.date()} | {e['gap_atr']:.2f} | {e['gap_pct'] * 100:.2f}% | {yn(e['gap_strict'])} "
          f"| {yn(e['close_below_prev_low'])} | {'—' if pd.isna(e['vol_ratio']) else f'{e.vol_ratio:.2f}'} "
          f"| {yn(e.get('peer_sync'))} | {yn(e.get('intl_sync'))} | {unf} "
          f"| {_pct(fwd.loc[d, 'r5'])} | {_pct(fwd.loc[d, 'r20'])} |")

    w("\n## 注意事項\n")
    w("- 這是事件研究，只回答「訊號之後市場表現是否比一般日子差」，還沒有計入減碼、恢復與交易成本（Phase 5）。")
    w("- 變體之間高度重疊（都是高檔跳空的子集合），不是獨立的檢定；多個變體同時看起來有效時，不代表證據加倍。")
    w("- 報酬以收盤計；實際執行最快是隔日開盤，隔日開盤本身可能已經反映部分跌幅。")
    w("- 國際同步使用 Yahoo 費半資料（非官方）。")
    return "\n".join(L) + "\n"
