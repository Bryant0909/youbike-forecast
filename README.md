# 🚲 YouBike Forecast（YouBike 站點借還預測）

> 預測某個 YouBike 站點 30 分鐘後會不會「借不到車」或「還不了車」。

## 目標

- **第一個成果**：持續收集台北 YouBike 即時資料
- **最終目標**：預測模型 + 網頁或 LINE Bot，並公開**上線後的真實準確率**

## 路線圖

| 階段 | 內容 | 產出 |
|---|---|---|
| 0 | 資料收集程式，每 5 分鐘抓一次 | `scripts/collect.py` 持續運作 |
| 1 | 探索性分析（EDA） | 哪些站最常空、尖峰時段在哪 |
| 2 | Baseline：「維持現狀」、「歷史同時段平均」 | 評估基準 |
| 3 | ML 模型（LightGBM）+ 天氣、假日、時段特徵 | 模型 + 誤差分析 |
| 4 | 上線：網頁或 LINE Bot | 可以實際使用 |
| 5 | 追蹤真實準確率、寫失敗案例 | 報告 |

## 換電腦 / 重新安裝

看 [`SETUP.md`](SETUP.md) —— 從全新電腦把整個專案（含累積的資料）還原的完整步驟。

## 專案結構

```
SETUP.md       換電腦時怎麼還原
.github/       GitHub Actions 排程（收集、壓縮、監控）
scripts/       資料收集（YouBike／天氣）、壓縮、覆蓋率檢查
src/youbike/   資料載入（data.py）、特徵工程、模型
pyproject.toml  讓 import youbike 能用（pip install -e .）
data/raw/      原始資料（不上傳 GitHub）
notebooks/     EDA 與實驗
docs/          設計決策
AI_LOG.md      AI 協作紀錄
```

## 自動化（全部跑在 GitHub Actions 上）

| 排程（台北時間） | 做什麼 | 程式 |
|---|---|---|
| 每 5 分鐘 | 抓一次全台北 1808 站的即時資料，存成 Parquet | `scripts/collect.py` |
| 每 5 分鐘 | 抓一次中央氣象署自動氣象站觀測（每 10 分鐘更新，重複的會跳過） | `scripts/collect_weather.py` |
| 每天 03:00 | 把前一天的小檔壓成日檔（YouBike 小 7 倍、天氣小 6 倍） | `scripts/compact.py` |
| 每天 03:30 | 檢查覆蓋率／空隙／尖峰時段／天氣，不足就自動開 Issue 通知 | `scripts/check_coverage.py` |

資料存在 [`data` 分支](https://github.com/Bryant0909/youbike-forecast/tree/data)，
程式和資料的 commit 歷史完全分開。

## 狀態

🚧 phase 0 進行中

- [x] 0-1 確認 data.taipei YouBike 2.0 即時 API 可用（1808 站）
- [x] 0-2 建立 GitHub public repo
- [x] 0-3 `scripts/collect.py` 收集程式
- [x] 0-4 GitHub Actions 每 5 分鐘排程（`.github/workflows/collect.yml`）
- [x] 0-5 每日壓縮 288 個小檔成一個日檔（`scripts/compact.py`，實測小 7 倍）
- [x] 0-6 每日覆蓋率檢查，不足就自動開 Issue（`scripts/check_coverage.py`）

資料收集中：[`data` 分支](https://github.com/Bryant0909/youbike-forecast/tree/data)
· [執行紀錄](https://github.com/Bryant0909/youbike-forecast/actions)
