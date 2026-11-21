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
scripts/       資料收集（YouBike／天氣）、壓縮、覆蓋率檢查、每月備份、
               跑 baseline、訓練模型（train_model.py）、特徵組實驗（ablation.py）、
               上線模型訓練（train_production.py）、上線預測（predict.py）
src/youbike/   資料載入（data.py、weather.py）、標籤與切分（dataset.py）、
               評估指標（evaluate.py）、baseline（baselines.py）、
               特徵（features.py、calendar_tw.py、citywide.py）、模型（model.py）、
               上線用的特徵與計數表（online.py、daily_low.py）
models/        上線用的模型和它的身分證（model_card.json）
site/          網頁（GitHub Pages）
tests/         特徵「不偷看未來」、線上 = 離線等檢查（python -m pytest tests/）
pyproject.toml  讓 import youbike 能用（pip install -e .）
data/raw/      原始資料（不上傳 GitHub）
notebooks/     EDA、推測驗證、模型分析
docs/          設計決策
AI_LOG.md      AI 協作紀錄
```

## 自動化（全部跑在 GitHub Actions 上）

| 排程（台北時間） | 做什麼 | 程式 |
|---|---|---|
| 每 5 分鐘（外部服務 cron-job.org 觸發） | 抓一次全台北 1808 站的即時資料，存成 Parquet | `scripts/collect.py` |
| 每 5 分鐘（同上） | 抓一次中央氣象署自動氣象站觀測（每 10 分鐘更新，重複的會跳過） | `scripts/collect_weather.py` |
| 每天 03:00 | 把前一天的小檔壓成日檔（YouBike 小 7 倍、天氣小 6 倍） | `scripts/compact.py` |
| 每天 03:30 | 檢查覆蓋率／空隙／尖峰時段／天氣，不足就自動開 Issue 通知 | `scripts/check_coverage.py` |
| 每月 2 號 14:00 | 上個月的資料備份到 [Release](https://github.com/Bryant0909/youbike-forecast/releases)，核對後把 data 分支瘦身 | `scripts/backup_month.py`、`scripts/slim_data_branch.sh` |

> 為什麼收集要靠外部服務觸發？GitHub 內建排程對這個 repo 極不可靠（實測 7.5 小時只觸發 1 次），
> 詳見 [`docs/DECISIONS.md`](docs/DECISIONS.md)。

資料存在 [`data` 分支](https://github.com/Bryant0909/youbike-forecast/tree/data)，
程式和資料的 commit 歷史完全分開。

## 狀態

🚧 資料累積中（從 2026-09-30 開始，目標 4~8 週）；phase 2 的評估框架已先建好

**phase 0：資料收集** ✅
- [x] 0-1 確認 data.taipei YouBike 2.0 即時 API 可用（1808 站）
- [x] 0-2 建立 GitHub public repo
- [x] 0-3 `scripts/collect.py` 收集程式
- [x] 0-4 GitHub Actions 每 5 分鐘收集（`.github/workflows/collect.yml`）
- [x] 0-5 每日壓縮 288 個小檔成一個日檔（`scripts/compact.py`，實測小 7 倍）
- [x] 0-6 每日覆蓋率檢查，不足就自動開 Issue（`scripts/check_coverage.py`）
- [x] 0-7 天氣資料收集（中央氣象署自動氣象站，每 10 分鐘）
- [x] 0-8 改由外部服務每 5 分鐘觸發（換上線後 19 小時 229 次全部成功、零漏收）
- [x] 每月備份到 GitHub Release + data 分支瘦身（`.github/workflows/backup.yml`）

**phase 1：探索性分析** ✅
- [x] 1-1 資料載入層（`src/youbike/data.py`）
- [x] 1-2 資料健全性檢查（`scripts/data_health.py`）
- [x] 1-3 EDA：哪些站最常空、尖峰時段在哪（[`notebooks/01_eda.ipynb`](notebooks/01_eda.ipynb)，10/1~10/18）
  - 建模範圍改成「大安區 + 臺大公館校區」（API 把臺大校園另列一區，只看大安區會把校園切成兩半）
  - 最重要的發現：大安區早上 10 點最難借、校區晚上 10 點最難借，兩區節奏相反
- [x] 1-4 驗證 EDA 的兩個推測（[`notebooks/02_hypotheses.ipynb`](notebooks/02_hypotheses.ipynb)）：半夜有調度車補車（大致確認）；10/13 那週少車是全市性的

**phase 2：Baseline**
- [x] 2-1 建標籤（30 分鐘後的答案；附近沒資料就跳過，不刪原始資料）
- [x] 2-2 照時間切資料（交界處 purge，避免偷看未來）
- [x] 2-3 評估指標：PR-AUC、固定精確率下的召回率
- [x] 2-4 Baseline ①「維持現狀」
- [x] 2-5 Baseline ②「歷史同時段平均」（`python scripts/run_baselines.py`）
- [x] 2-6 用 4 週資料重跑、定案預測目標：**30 分鐘後可借 <= 1**（測試期發生率 22%；維持現狀 PR-AUC 0.643、歷史同時段平均 0.572）

**phase 3：第一個模型（LightGBM）** ✅ —— 分析在 [`notebooks/03_model.ipynb`](notebooks/03_model.ipynb)
- [x] 3-1 安裝 LightGBM、scikit-learn
- [x] 3-2 ~ 3-6 特徵（`src/youbike/features.py`）：這一站自己的狀態、日曆與國定假日、站的類型、全台北總數、天氣；
  `tests/test_features.py` 用「刪掉未來重算、特徵必須不變」檢查不偷看未來
- [x] 3-7 LightGBM 跟 baseline 同一張成績單（`python scripts/train_model.py`）
- [x] 3-8 三段測試期都贏（`--folds`）；固定精確率的指標定案 recall@P70
- [x] 3-9 特徵組實驗（`scripts/ablation.py`）與錯誤分析
- [x] 3-10 文件與成果網頁

| 測試期 PR-AUC（<= 1，三段平均） | 全天 | 尖峰 | 大安區 | 校區 |
|---|---|---|---|---|
| ① 維持現狀 | 0.648 | 0.566 | 0.547 | 0.802 |
| ② 歷史同時段平均 | 0.553 | 0.538 | 0.422 | 0.706 |
| **③ LightGBM** | **0.780** | **0.697** | **0.668** | **0.893** |

在「說會沒車時，10 次至少對 7 次」的前提下，LightGBM 能提前抓到約 **72%** 的借不到（維持現狀 38%）。

**phase 4：上線（網頁）**
- [x] 4-1 架構：GitHub Pages 網頁 + 預測跑在收集的同一個 job + 網頁直接讀 data 分支上的預測檔
- [x] 4-2 線上特徵（`src/youbike/online.py`）：重用離線的 `build_features()`，`tests/test_online.py` 保證線上 = 離線
- [x] 4-3 站的歷史計數表（`src/youbike/daily_low.py`）：每天補一次，預測時查表
- [x] 4-4 上線模型（`python scripts/train_production.py`）：兩階段訓練，模型與 `model_card.json` 放 `models/`
- [x] 4-5 上線預測（`scripts/predict.py`）：輸出 `predictions/latest.json` 與每次的預測紀錄
- [x] 4-6 接進 `collect.yml`；每日覆蓋率檢查會盯「有預測的時刻佔幾成」
- [x] 4-7 網頁（`site/`）：地圖、清單、搜尋、資料過舊警告
- [x] 4-8 上線檢查與文件

上線之後的流程：

```
cron-job.org 每 5 分鐘 → collect.yml：收集 → predict.py → 推回 data 分支
                                              ├─ predictions/latest.json ← 網頁每 2.5 分鐘讀一次
                                              └─ predictions/<日期>/…     ← 之後對答案算真實準確率（phase 5）
```

**每月重新訓練模型**：`python scripts/train_production.py`，確認 `models/model_card.json` 的驗證分數合理後開 PR 合併；
合併後下一次收集就會用新模型。程式改了特徵卻沒重訓的話，預測會直接停下來（不會用錯的特徵亂猜）。

資料收集中：[`data` 分支](https://github.com/Bryant0909/youbike-forecast/tree/data)
· [執行紀錄](https://github.com/Bryant0909/youbike-forecast/actions)
