# tw-trend-monitor

個人使用的台股多空趨勢與資金風險監控系統。透過短、中、長線市場指標，辨識台股趨勢轉弱的早期訊號，協助管理持股水位。

目前階段：**Phase 1 進行中**：期貨籌碼抓取已完成。

## 文件

- [docs/spec-v1.1-changes.md](docs/spec-v1.1-changes.md)：規格 v1.1 修訂（轉空事件定義、計分架構、百分位門檻、Phase 1 範圍）
- [docs/data-feasibility.md](docs/data-feasibility.md)：逐項資料來源實測結果與最低成本資料架構

## 安裝

```bash
pip install -e ".[dev]"
```

資料庫預設在 `data/twtm.db`，可用環境變數 `TWTM_DB` 或 `--db` 指定。

## 期貨籌碼

商品：臺股期貨（TX）、小型臺指（MTX）、微型臺指（TMF）。

```bash
twtm futures backfill            # 首次：回補期交所 3 年視窗（約 5 分鐘，可中斷續跑）
twtm futures update              # 每日：重抓最近 10 天（台灣時間 15:00 後執行可取得當日資料）
twtm futures show --days 20      # 外資淨未平倉、散戶多空比
twtm futures status              # 各來源資料涵蓋範圍
```

期交所只保留最近 3 年，且是滾動視窗，每過一天最舊的一天就取不到。更早的資料用 FinMind 補：

```bash
export FINMIND_TOKEN=...         # 免費註冊取得；匿名額度很小
twtm futures backfill --source finmind --start 2020-01-01
```

額度用盡時會中止，重跑會從中斷處繼續。期交所與 FinMind 資料重疊時，以期交所為準。

### 散戶多空比口徑

```
全市場 OI    = 一般時段所有到期月份（含週契約、不含價差組合）未沖銷契約數加總
散戶多空比   = (三大法人空單 − 三大法人多單) / 全市場 OI
```

正值＝非法人偏多。「散戶」實為「非三大法人」。任一身分別或 OI 缺漏時顯示「資料不全」，不以 0 代替。

## 開發

```bash
pytest                           # 離線測試，使用 tests/fixtures 的真實回應
python feasibility/probe.py      # 檢測所有資料來源是否可用
```

## 免責

本系統僅為輔助判斷，不構成投資建議，不提供自動交易。
