# CLAUDE.md — YouBike Forecast

## 專案背景
- 預測台北 YouBike 站點 30 分鐘後會不會「借不到車」，個人 GitHub 作品集專案（偏 ML）
- 擁有者是 ML 初學者：寫程式時請**加上清楚的中文註解**，每完成一步用白話解釋做了什麼、為什麼
- 請先讀 `README.md`（路線圖）、`PREP.md`（準備清單）、`docs/DECISIONS.md`（決策紀錄）

## 技術規範
- Python 3.11+，收集程式放 `scripts/`，模型相關放 `src/youbike/`
- 資料存 Parquet，放 `data/raw/`（已在 .gitignore，不上傳）
- API key 放 `.env`，**絕對不要寫進程式碼或 commit**
- 資料切分一律**照時間切**，不可隨機打亂（避免偷看未來）
- 任何模型都要跟 baseline 比較：「維持現狀」、「歷史同時段平均」

## 工作方式
- **一次只做一步（one step），做完就停下來讓我確認** —— 不是一次做完一整個 phase
- 步驟用 `phase 0-1`、`phase 0-2`、`phase 1-1` … 的編號方式；每個 phase 開始前先把它拆成步驟列表給我看
- 每一步開始前先講「這一步要做什麼」，做完後用白話講「做了什麼、為什麼、下一步是什麼」，然後停下來等我確認
- 重要決定寫進 `docs/DECISIONS.md`，AI 產生的主要內容記錄到 `AI_LOG.md`

## 已定案的決定（詳見 `docs/DECISIONS.md`）
- **收集範圍**：全台北市約 1800 站全存（實測 1808~1813 站）（API 一次就回傳全部，存全部的成本接近零）
- **建模範圍**：第一個模型只做一區（大安區），避免一開始太複雜
- **存放方式**：兩段式 —— 每 5 分鐘寫一個小 Parquet；每日凌晨壓縮成一個日檔並刪掉小檔；放在 repo 的 `data` 分支；每月備份一份到 GitHub Release
- **執行環境**：GitHub Actions，repo 必須設 **public**（私有 repo 每月 2000 分鐘額度會爆掉）
- **收集觸發**：GitHub 內建排程對這個 repo 不可靠，收集改由外部服務 cron-job.org 每 5 分鐘打 `workflow_dispatch`（token 只給 Actions 寫入權限）
- **預測目標**：等資料滿 4 週、重跑 baseline 後定案（原始資料存的是車輛數，標籤隨時可重算）；目前傾向「可借車輛 <= 1」。實測發生率：<= 0 約 4~7%、<= 1 約 10~16%（大安區）
- **不做本機備援收集**（擁有者下班後家裡沒網路，只能涵蓋非尖峰時段，效益低）；改成每日檢查覆蓋率，不足就自動開 Issue 用 email 通知
- **評估指標**：這是不平衡問題，不看 accuracy，看 PR-AUC 或「固定精確率下的召回率」

## 目前狀態（2026-10-07）
- **phase 0 收集已完成**（0-1 ~ 0-8 + 每月備份），細節見 `README.md` 的狀態清單
- 資料從 2026-09-30 開始累積，**建模要等 4~8 週**；在那之前做不依賴資料量的事
- phase 2 的評估框架已先建好：`src/youbike/dataset.py`（標籤、時間切分）、`evaluate.py`、`baselines.py`、`scripts/run_baselines.py`
- 天氣雨量 `precip_mm` 是**日累積值**，建模一律用 `youbike.weather.rain_increment()` 換算後的 `rain_mm`

## 接下來
- [ ] **1-3** EDA：哪些站最常空、尖峰時段在哪（資料多一點再做）
- [ ] **2-6** 資料滿 4 週後重跑 baseline，定案預測目標（可借 <= 0 或 <= 1）
- [ ] **phase 3** LightGBM + 天氣、假日、時段特徵（屆時再裝 scikit-learn、lightgbm）
