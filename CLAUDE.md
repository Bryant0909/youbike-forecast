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
- **建模範圍**：第一個模型只做「大安區 + 臺大公館校區」（約 280 站，`youbike.data.MODEL_DISTRICTS`），避免一開始太複雜。API 把臺大校園的站另外標成「臺大公館校區」，只篩大安區會把校園切成兩半
- **存放方式**：兩段式 —— 每 5 分鐘寫一個小 Parquet；每日凌晨壓縮成一個日檔並刪掉小檔；放在 repo 的 `data` 分支；每月備份一份到 GitHub Release
- **執行環境**：GitHub Actions，repo 必須設 **public**（私有 repo 每月 2000 分鐘額度會爆掉）
- **收集觸發**：GitHub 內建排程對這個 repo 不可靠，收集改由外部服務 cron-job.org 每 5 分鐘打 `workflow_dispatch`（token 只給 Actions 寫入權限）
- **預測目標（2026-11-03 定案）**：「30 分鐘後可借車輛 <= 1」，<= 0 當輔助指標一起報。測試期發生率 <= 1 約 22%（大安區平日 19%、校區平日 40%）、<= 0 約 11%
- **不做本機備援收集**（擁有者下班後家裡沒網路，只能涵蓋非尖峰時段，效益低）；改成每日檢查覆蓋率，不足就自動開 Issue 用 email 通知
- **評估指標**：這是不平衡問題，不看 accuracy。主指標 PR-AUC，固定精確率的指標用 **recall@P70**（2026-11-04 定案）；成績單要分全天／尖峰／大安區／校區，並看三段測試期（`train_model.py --folds`）

## 目前狀態（2026-11-22）
- **phase 0 收集已完成**（0-1 ~ 0-8 + 每月備份），細節見 `README.md` 的狀態清單
- **phase 1 已完成**：EDA 在 `notebooks/01_eda.ipynb`、推測驗證在 `02_hypotheses.ipynb`，結論與 phase 3 特徵方向見 `docs/DECISIONS.md` 2026-10-19
- 網頁版 EDA 的資料用 `python scripts/export_dashboard.py` 重新匯出（輸出在 `data/processed/dashboard/`，再請 Claude 上傳）
- **phase 2 已完成**（2-6 定案見 `docs/DECISIONS.md` 2026-11-03）：`src/youbike/dataset.py`（標籤、時間切分）、`evaluate.py`、`baselines.py`、`scripts/run_baselines.py`
- **phase 3 已完成**：特徵 `src/youbike/features.py`（+ `calendar_tw.py`、`citywide.py`、`weather.weather_features`）、模型 `model.py`、`scripts/train_model.py`、`scripts/ablation.py`、分析 `notebooks/03_model.ipynb`。三段測試期平均 PR-AUC：LightGBM 0.780、維持現狀 0.648；recall@P70 0.718 vs 0.375
- **phase 4 上線（網頁）已完成**（2026-11-22 上線，https://bryant0909.github.io/youbike-forecast/ ，Pages 來源 = GitHub Actions）：架構見 `docs/DECISIONS.md` 2026-11-22。`collect.yml` 收集完跑 `scripts/predict.py`（continue-on-error），輸出到 data 分支 `predictions/`；網頁在 `site/`（`pages.yml` 部署），直接讀 raw.githubusercontent.com 上的 `predictions/latest.json`。上線模型在 `models/`（`train_production.py` 產生，每月手動重訓、用 PR 更新）
- **改特徵 = 要重訓上線模型**：predict.py 會比對 `model_card.json` 的特徵清單，不一致就停止預測。改了 `features.py` 一定要跑 `train_production.py` 並一起 commit
- **特徵不能偷看未來**：新增特徵後一定要跑 `python -m pytest tests/`（「刪掉未來重算、特徵必須不變」，以及「線上 = 離線」）。用整段歷史算的特徵一律「只用 t 那天以前的日子」；天氣用 `fetched_at` 對齊（觀測要 10~25 分鐘後才拿得到）
- 天氣雨量 `precip_mm` 是**日累積值**，建模一律用 `youbike.weather.rain_increment()` 換算後的 `rain_mm`
- 資料從 2026-09-30 開始累積，10/22~10/26 收集中斷，切時間要注意這段空窗

## 接下來
- [ ] **上線後的第一週**：確認 Actions 的預測步驟沒有黃色警告、每日覆蓋率報告的「模型預測」≥ 90%、網頁正常更新
- [ ] **phase 5** 追蹤真實準確率：用 `predictions/` 的紀錄對 30 分鐘後的實際車數，算上線後的 PR-AUC、recall@P70，寫失敗案例 —— 開始前先拆步驟
- [ ] **每月重訓上線模型**（下一次約 12/21）：`python scripts/train_production.py`，開 PR
- [ ] LINE Bot（選擇性）：讀同一份 `predictions/latest.json`
- [ ] **下一輪特徵**（3-9 錯誤分析）：附近站的狀態（改善「突然被借光」）、捷運人流、臺大行事曆
- [ ] 資料滿 2~3 個月（雨天變多）後重跑 `scripts/ablation.py`，決定全市總數、天氣兩組特徵的去留（目前 +0.001，沒有幫助）
- [ ] **2027 年底前**把 2028 年放假日加進 `src/youbike/calendar_tw.py`（2026、2027 已加；超出範圍會直接報錯、預測停擺）
