#!/usr/bin/env bash
#
# data 分支瘦身：把 data 分支換成「只有一個 commit、檔案內容完全相同」的新分支。
#
# ┌─ 為什麼要做 ───────────────────────────────────────────────────────┐
# │ 每 5 分鐘推一次 = 每天 288 個 commit。每日壓縮只把「目前的檔案」    │
# │ 變小，git 歷史裡那些已經被壓縮掉的小檔永遠留著 —— 實測 8 天就有    │
# │ 1,846 個 commit、repo 約 14.5 MB，而目前的檔案只有 6.4 MB。         │
# │ 放著不管，半年後每次 clone 都要下載好幾百 MB 的歷史。               │
# │                                                                    │
# │ 那些歷史裡的小檔，內容早就全部壓進日檔了（compact.py 核對過才刪），│
# │ 而且每月也備份到 Release 了，所以丟掉歷史不會丟任何資料。          │
# └────────────────────────────────────────────────────────────────────┘
#
# ┌─ 安全設計 ─────────────────────────────────────────────────────────┐
# │ 1. 先暫停 collect、等正在跑的收集結束 —— 不然它推到一半會撞上      │
# │    （它手上的是舊歷史，跟新分支接不起來）。結束時一定恢復（trap）。│
# │ 2. 新 commit 直接用舊分支最新的「tree」（檔案快照）建立，           │
# │    所以檔案內容保證一個 byte 都不差；推完再核對一次 tree 一樣。    │
# │ 3. 用 --force-with-lease：只有在遠端還是我們看到的那個版本時才覆蓋，│
# │    如果中間有人（例如 compact）推了新東西，推送會被拒絕、什麼都不改。│
# └────────────────────────────────────────────────────────────────────┘
#
# 需要：gh 已登入（Actions 裡用 GH_TOKEN），權限 contents: write + actions: write
#
# 用法：
#   bash scripts/slim_data_branch.sh <data 分支的 git 目錄>

set -euo pipefail

DATA_DIR="${1:?用法: slim_data_branch.sh <data 分支的 git 目錄>}"
COLLECT_WF="collect.yml"

log() { echo "[$(date -u +%H:%M:%SZ)] $*"; }

# --- 1. 暫停收集，而且保證結束時一定恢復 -------------------------------
# trap EXIT：不管這支程式是成功、失敗、還是中途出錯結束，都會跑這一行。
# 最怕的情況是「瘦身失敗 + 收集一直停著沒人發現」，所以這行是整支最重要的。
gh workflow disable "$COLLECT_WF"
trap 'log "恢復 collect"; gh workflow enable "$COLLECT_WF"' EXIT
log "已暫停 collect，等正在跑／排隊中的收集結束…"

for i in $(seq 1 60); do
  active=$(gh run list -w "$COLLECT_WF" --limit 20 --json status \
           --jq '[.[] | select(.status != "completed")] | length')
  [ "$active" = "0" ] && break
  log "還有 $active 個收集在跑，10 秒後再看（$i/60）"
  sleep 10
done
if [ "$active" != "0" ]; then
  log "等了 10 分鐘還有收集在跑，這次放棄（什麼都沒改）"
  exit 1
fi

# --- 2. 用最新的檔案快照建立一個沒有歷史的 commit ------------------------
cd "$DATA_DIR"
git config user.name  "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"

git fetch --depth=1 origin data
OLD_TIP=$(git rev-parse FETCH_HEAD)
TREE=$(git rev-parse "FETCH_HEAD^{tree}")
N_FILES=$(git ls-tree -r --name-only "$TREE" | wc -l | tr -d ' ')

# commit-tree：直接拿一個 tree 做出 commit，不指定 parent = 沒有歷史（孤兒）
NEW=$(git commit-tree "$TREE" -m "data: 瘦身（$(date -u +%Y-%m-%d)），歷史已備份到 GitHub Release

舊的最新 commit：$OLD_TIP
檔案數：$N_FILES（內容與舊分支完全相同）")
log "舊：$OLD_TIP  新：$NEW  檔案數：$N_FILES"

# --- 3. 只有在遠端還是 OLD_TIP 時才覆蓋 -----------------------------------
git push --force-with-lease="data:$OLD_TIP" origin "$NEW:refs/heads/data"

# --- 4. 核對：遠端的檔案快照必須跟瘦身前一模一樣 ---------------------------
git fetch --depth=1 origin data
if [ "$(git rev-parse "FETCH_HEAD^{tree}")" != "$TREE" ]; then
  log "❌ 推上去之後檔案快照不一致！舊的 commit 是 $OLD_TIP，請手動檢查"
  exit 1
fi
log "✅ 瘦身完成：data 分支現在只有 1 個 commit，$N_FILES 個檔案，內容與之前完全相同"
