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
