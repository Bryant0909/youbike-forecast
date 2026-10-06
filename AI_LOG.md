# AI 協作紀錄

記錄哪些部分由 AI 產生、你做了哪些判斷和修正。這是這個 repo 的亮點之一。

| 日期 | 做了什麼 | AI 產生的部分 | 我修正／決定的部分 |
|---|---|---|---|
| 2026-09-29 | 專案整體討論、定下 phase 0 的技術決策 | 盤點現況；指出 Actions cron 會跳過執行、私有 repo 額度會爆、兩段式儲存的容量估算、不平衡問題不能看 accuracy；提出「收集範圍 ≠ 建模範圍」 | 選定兩段式 + repo data 分支、全台北都收但先建模一區；指出自己下班後沒網路，因此否決本機備援收集器（改為斷線監控） |
| 2026-09-29 | 改 `CLAUDE.md` 工作方式 | 改寫成 phase 0-1 / 0-2 的步驟編號制 | 要求從「一次一個 phase」改成「一次一步」 |
| 2026-09-29 | phase 0-1：確認 API 可用 | `scripts/check_api.py`（試兩個來源、印範例、列欄位、檢查資料新舊與行政區）+ 資料品質探查 | 待確認 |
| 2026-09-30 | phase 0-4：GitHub Actions 每 5 分鐘排程 | 建 `data` 孤兒分支（用底層指令避開 `--orphan` 的刪檔風險）、`.github/workflows/collect.yml`（雙 checkout、concurrency、push 重試）、本機完整模擬驗證、指出 data 分支 git 歷史會膨脹約 97 MB/月 | 待確認 |
| 2026-09-30 | phase 0-5：每日壓縮 | `scripts/compact.py`（四步式先核對才刪檔）、`scripts/push_data.sh`（抽出共用推送邏輯）、`.github/workflows/compact.yml`；用 288 個擬真檔量出真實壓縮率、用假的 origin 實測 push 競爭與 rebase 重試、用壞檔驗證中止行為；抓到 git 改名偵測導致 commit 訊息錯誤的 bug | 待確認 |
| 2026-09-30 | phase 0-6：每日覆蓋率檢查與自動警報 | `scripts/check_coverage.py`（覆蓋率／最大連續空隙／尖峰時段／資料新鮮度／站數 五項檢查）、`.github/workflows/coverage.yml`（一個問題一個 Issue 串、恢復自動關閉）；指出 GitHub 預設通知設定會讓機器人開的 Issue 收不到 email，必須指派並 @ 本人；造了五種缺漏情境實測，其中「整天 89.9% 但尖峰只有 33%」證明了為什麼不能只看筆數 | 待確認 |
| 2026-09-30 | 實測 phase 0-6 的通知管道、診斷排程未觸發 | 手動觸發 coverage 驗證兩條路徑（開新 Issue 含 @ 與指派、同問題改留言不重複開）；系統性排除排程問題（repo 設定、workflow 狀態、cron 語法、檔案 BOM、帳號年齡、GitHub 平台事故）後判斷是 GitHub 排程器尚未接手 | 選擇「先等並設檢查點」而不是立刻改架構 |
| 2026-09-30 | phase 1-1：資料載入層 | `src/youbike/data.py`（自動找資料位置、吸收壓縮／未壓縮兩種形式、行政區篩選、時間欄位、去重、資料盤點報告）、`pyproject.toml`；指出用 5 個時間點做 EDA 沒有意義，phase 1 先只做載入層；造了時間戳記正確的三天測試資料實測，抓到 `available_days()` 因 `listdir` 排序而漏掉 100 個小檔的靜默資料遺失 bug，以及誤以為 `stations.parquet` 有 `total_docks` 欄位的錯 | 待確認 |
| 2026-09-30 | 確認 GitHub 排程不可用、改走外部觸發 | 用一個最陽春的心跳 workflow 做對照實驗，證明問題不在 `collect.yml` 而在 repo 的排程器；三個 workflow 加上 `repository_dispatch` 觸發點並用 API 實測通過；指出排程壞掉會連帶讓壓縮與監控失效，因此外部觸發必須一起涵蓋 | 選定方案 ①（外部排程服務），要自行建 PAT 與註冊服務 |
| 2026-09-30 | phase 0-7：天氣資料收集 | 先探勘三支氣象署 API 再選型（時間解析度 10 分鐘 vs 每小時、是否涵蓋大安區）、`scripts/collect_weather.py`（沿用檔名去重與動靜分離、-99 轉 null、WGS84 座標）、接進 collect.yml 並加 `continue-on-error` 保護主要資料；指出 `Now.Precipitation` 語意未確認、在驗證前不該拿來做特徵 | 待確認 |
| 2026-09-30 | 改用長時間執行的收集迴圈 | `scripts/collect_loop.sh` 與改寫 `collect.yml`（timeout 350 分鐘、每輪獨立接錯、每輪推送、睡眠扣除耗時避免漂移）；本機實測 3 輪間隔無漂移 | 卡在註冊外部服務，決定改用完全不需使用者操作的方案；已提醒撤銷誤貼在對話中的 PAT |
| 2026-10-01 | 修正覆蓋率檢查對「進行中的日子」的誤報 | 發現手動檢查今天時，整天分母會把「未來還沒發生」報成大規模缺漏；加入 `day_bounds()` 只統計到現在、尖峰時段細分已完成／進行中／還沒到、取樣點數改無條件進位；驗證昨日報告數字完全不變 | 待確認 |
| 2026-10-01 | phase 1-2：資料健全性檢查 | `scripts/data_health.py`（七項檢查：取樣間隔、資料延遲、車輛數是否真的在變、死站、目標發生率、時段分布、是否集中在少數站）；用真實資料推翻先前「可借=0 只有 2~5%」的假設（實測 8.0%）、確認目標不是退化的、量出 96 秒的資料延遲 | 待確認 |
| 2026-10-01 | A：壓縮與監控延伸到天氣資料 | `compact.py` 改成規格表驅動（而非複製一份天氣版）、`check_coverage.py` 新增天氣段落並把 `load_day` 泛用化；測試含 YouBike 回歸、`--dataset` 篩選、欄位不符中止且離開碼為 1、無天氣資料夾、已壓縮日檔、進行中的日子 | 待確認 |
| 2026-10-06 | 檢查近期收集表現、phase 0-8a 改回外部觸發 | 用 `check_coverage.py` 跑 10/1~10/6 的報告、從 Actions 紀錄找出 10/6 凌晨 334 分鐘空洞的原因（長迴圈結束後下一次派不到機器、排程 4.5 小時沒再來）；`collect.yml` 改回單次執行、只留 `workflow_dispatch`；選 `workflow_dispatch` 以把外部 token 權限壓到 Actions 寫入 | 先 commit 不 push，等擁有者設好 cron-job.org 再上線，避免中間空窗 |
