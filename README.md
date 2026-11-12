# tw-trend-monitor

個人使用的台股多空趨勢與資金風險監控系統。透過短、中、長線市場指標，辨識台股趨勢轉弱的早期訊號，協助管理持股水位。

目前階段：**資料可行性評估完成，尚未開始實作。**

## 文件

- [docs/spec-v1.1-changes.md](docs/spec-v1.1-changes.md)：規格 v1.1 修訂（轉空事件定義、計分架構、百分位門檻、Phase 1 範圍）
- [docs/data-feasibility.md](docs/data-feasibility.md)：逐項資料來源實測結果與最低成本資料架構

## 工具

```bash
pip install requests
python feasibility/probe.py
```

逐一檢測所有資料來源是否可用。建議在實際排程的環境（家用電腦或 GitHub Actions）跑一次，確認國發會、BLS 等在雲端被擋的來源。

## 免責

本系統僅為輔助判斷，不構成投資建議，不提供自動交易。
