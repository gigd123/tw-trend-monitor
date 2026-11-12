# tw-trend-monitor

個人使用的台股多空趨勢與資金風險監控系統。透過短、中、長線市場指標，辨識台股趨勢轉弱的早期訊號，協助管理持股水位。

目前階段：**期貨避險回測 hedge-v1 完成、前向紀錄進行中**。最新結果見 [reports/hedge-v1/summary.md](reports/hedge-v1/summary.md)。

## 文件

- [docs/ideas/333-growth-screen.md](docs/ideas/333-growth-screen.md)：分析師「333 成長選股法」評估（構想，尚未實作）
- [docs/mvp-spec-v1.2.md](docs/mvp-spec-v1.2.md)：**目前的 MVP 開發規格**（短線風險預警定位、Phase 劃分、訊號定義、減碼回測、資料表）
- [docs/spec-v1.1-changes.md](docs/spec-v1.1-changes.md)：規格 v1.1 修訂（轉空事件定義、計分架構、百分位門檻、Phase 1 範圍）
- [docs/data-feasibility.md](docs/data-feasibility.md)：逐項資料來源實測結果與最低成本資料架構

## 安裝

```bash
pip install -e ".[dev]"
```

Windows 也可以執行（已加入 `tzdata` 依賴，提供 Windows 缺少的時區資料）。

資料庫預設在 `data/twtm.db`，可用環境變數 `TWTM_DB` 或 `--db` 指定。

## 價格資料

標的：加權指數、櫃買指數、台積電（2005 起），以及費半、Nasdaq（國際同步條件用）。台積電另抓除權息資料，計算還原價並標記除權息日。

```bash
twtm prices backfill             # 首次：回補 2005 至今（證交所限速，約 1～2 小時，可中斷續跑）
twtm prices update               # 每日：重抓最近 35 天
twtm prices check                # 品質檢查：缺交易日、缺 OHLC、高低價矛盾、異常變動
```

## 訊號研究（Phase 2）

三個訊號系列（定義見 [mvp-spec-v1.2 §4、§4A、§4B](docs/mvp-spec-v1.2.md)）：

| 系列 | 版本 | 內容 |
|---|---|---|
| `gap` | gap-v2 | 高檔跳空向下 |
| `ma` | ma-v1 | 跌破均線、均線死叉、20 日線下彎、連續收低 |
| `breakout` | fb-v1 | 假突破、二次創高假突破、假突破後跳空 |

參數在 `src/twtm/signals/default.toml`；改參數要升級該系列的 `version`，舊版設定另存凍結（如 `gap-v1.toml`）。

```bash
twtm research run --family ma    # 研究報告 → reports/<版本>/<標的>.md（gap | ma | breakout）
twtm research run --family gap --config src/twtm/signals/gap-v1.toml   # 重現舊版
twtm signals compute             # 計算所有系列的訊號，寫入 signal_event
twtm signals recent --days 30    # 最近的訊號
```

研究以「訊號後 20 日內回檔 ≥ 4 ATR 的機率」判定（research-v2）：訊號是警訊，不是預測下跌。
每個變體分探索期（2005–2018）與驗證期（2019–）比較，判定規則在看結果前固定。

## 價格風險分數與減碼回測

價格趨勢轉弱程度（pw-v1）：由「跌破 20 日線後站不回」「5 日線在 20 日線之下」「20 日線下彎」三個狀態合成，
分為正常、初步轉弱、明顯轉弱、趨勢惡化。

```bash
twtm risk show --days 15         # 最近的轉弱程度
twtm backtest run --symbol TAIEX --symbol TPEX   # 目前 bt-v2：9 組參數 × 2 成本情境 × 1,000 次隨機警報 → reports/bt-v2/
twtm backtest run --config src/twtm/signals/bt-v1.toml   # 重現 bt-v1
twtm backtest hedge              # 期貨避險 hedge-v1 → reports/hedge-v1/（需要台指期 2005 起行情與加權報酬指數）
```

參數在 `src/twtm/signals/default.toml` 的 `[risk]`、`[backtest]`、`[hedge]`。

期貨避險回測需要的資料：

```bash
twtm futures backfill --dataset daily --product TX --start 2005-01-01
```

```bash
twtm prices backfill --only twse.total_return.TAIEX_TR --only finmind.total_return.TAIEX_TR
```

## 期貨籌碼

商品：臺股期貨（TX）、小型臺指（MTX）、微型臺指（TMF）。

```bash
twtm futures backfill            # 首次：回補期交所 3 年視窗（約 5 分鐘，可中斷續跑）
twtm futures update              # 每日：重抓最近 10 天（台灣時間 15:00 後執行可取得當日資料）
twtm futures show --days 20      # 外資淨未平倉、非法人多空比（估算）
twtm futures status              # 各來源資料涵蓋範圍
```

期交所只保留最近 3 年，且是滾動視窗，每過一天最舊的一天就取不到。更早的資料用 FinMind 補：

```bash
export FINMIND_TOKEN=...         # 免費註冊取得；匿名額度很小
twtm futures backfill --source finmind --start 2020-01-01
```

額度用盡時會中止，重跑會從中斷處繼續。期交所與 FinMind 資料重疊時，以期交所為準。

### 非法人多空比口徑（估算）

```
全市場 OI    = 一般時段所有到期月份（含週契約、不含價差組合）未沖銷契約數加總
非法人多空比 = (三大法人空單 − 三大法人多單) / 全市場 OI
```

正值＝非三大法人偏多。這是估算值，不等於散戶帳戶的實際多空比。任一身分別或 OI 缺漏時顯示「資料不全」，不以 0 代替。

## 開發

```bash
pytest                           # 離線測試，使用 tests/fixtures 的真實回應
python feasibility/probe.py      # 檢測所有資料來源是否可用
```

## 每日自動執行（Windows）

```bash
python -m venv .venv
```

```bash
.venv\Scripts\activate
```

```bash
pip install -e .
```

```bash
twtm futures backfill
```

```bash
twtm prices backfill
```

首次回補完成後，用工作排程器每個交易日 15:40 執行 `scripts\daily.bat`（期貨 → 價格 → 訊號 → 前向紀錄，輸出寫到 `data\daily.log`）：

```bash
schtasks /Create /TN "twtm daily" /TR "\"%CD%\scripts\daily.bat\"" /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 15:40
```

可以再加一個 21:40 的排程補抓晚間公布的資料（重複執行不會產生重複紀錄）。

### 前向紀錄

`twtm forward record`（每日流程會自動執行）把當天收盤後的轉弱程度，以及 bt-v1、bt-v2 所有參數組合的減碼層級寫進 `forward_log`。
**只新增、不覆寫**；超過下一個交易日 09:00 才記錄的會標記為補記。從 2026-11-12 開始，這是唯一完全沒被研究使用過的驗證資料。

```bash
twtm forward show --days 20
```

## 免責

本系統僅為輔助判斷，不構成投資建議，不提供自動交易。
