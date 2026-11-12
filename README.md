# tw-trend-monitor

個人使用的台股多空趨勢與資金風險監控系統。透過短、中、長線市場指標，辨識台股趨勢轉弱的早期訊號，協助管理持股水位。

目前階段：**Phase 2 訊號研究**。價格資料（2005 起）與期貨籌碼已完成；第一批訊號研究結果見 [reports/gap-research-summary.md](reports/gap-research-summary.md)。開發規格見 [docs/mvp-spec-v1.2.md](docs/mvp-spec-v1.2.md)。

## 文件

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

第一批：高檔跳空向下系列（定義見 [mvp-spec-v1.2 §4](docs/mvp-spec-v1.2.md)）。參數在 `src/twtm/signals/default.toml`，改參數要同時改 `version`。

```bash
twtm research gaps --symbol TAIEX --out reports/gap-research-taiex.md   # 事件研究報告
twtm signals compute             # 計算訊號，寫入 signal_event
twtm signals recent --days 30    # 最近的訊號
```

研究報告列出每個變體在探索期（2005–2018）與驗證期（2019–）的表現，判定規則在看結果前固定。

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

## 免責

本系統僅為輔助判斷，不構成投資建議，不提供自動交易。
