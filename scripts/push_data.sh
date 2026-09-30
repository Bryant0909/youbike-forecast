#!/usr/bin/env bash
#
# 把 data-branch/ 裡的變更 commit 並推回 GitHub 的 data 分支。
#
# 為什麼要抽成一支共用的 script？
#   collect.yml（每 5 分鐘）和 compact.yml（每天凌晨）都要做完全一樣的事。
#   如果在兩個 YAML 裡各寫一份，改了一邊忘了另一邊，就會出現「一個 workflow
#   有重試、另一個沒有」這種很難察覺的不一致。放在一個檔案裡只有一份真相。
#
# 用法：
#   bash scripts/push_data.sh "<commit 訊息前綴>" [<資料分支目錄>]
#
# 例：
#   bash scripts/push_data.sh data        # -> commit 訊息像 "data: snapshots/... (+1)"
#   bash scripts/push_data.sh compact     # -> commit 訊息像 "compact: snapshots/2026-09-27.parquet (+1 -288)"

# set -e：任何指令失敗就整支中止（不要帶著錯誤繼續跑）
# set -u：用到沒定義的變數就報錯（打錯變數名不會默默變成空字串）
# set -o pipefail：管線中間失敗也算失敗（不然只看最後一個指令的結果）
set -euo pipefail

PREFIX="${1:?用法: push_data.sh <commit 訊息前綴> [資料分支目錄]}"

# 預設目錄：從這支 script 的位置往上一層，再進 data-branch/
# （跟 Python 那邊一樣的做法：不依賴「執行時在哪個資料夾」）
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DATA_DIR="${2:-$REPO_ROOT/data-branch}"

cd "$DATA_DIR"

# commit 掛在 Actions 機器人名下，跟你自己手寫的 commit 分得開
git config user.name  "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"

# 沒有任何變更就直接結束。
# 這不是錯誤（代表來源還沒更新，或是沒有日子需要壓縮），所以用 exit 0。
if [ -z "$(git status --porcelain)" ]; then
  echo "沒有變更，這次不 commit"
  exit 0
fi

git add -A

# 組一個看得懂的 commit 訊息：前兩個新增的檔名 + 新增/刪除的數量。
# 這樣在 GitHub 上看 data 分支的歷史，一眼就知道每個 commit 做了什麼。
#
# 為什麼要加 --no-renames？
#   git 預設會做「改名偵測」：如果新增的檔案跟被刪掉的檔案內容很像，
#   它會判定成「改名」而不是「新增 + 刪除」。壓縮的時候正好會踩到 ——
#   日檔的內容跟小檔高度相似，於是日檔被算成改名、--diff-filter=A 抓不到它，
#   commit 訊息就變成沒意義的 "update (+0 -287)"。
#   關掉改名偵測之後，新增就是新增、刪除就是刪除，數字才對得上。
DIFF="git diff --cached --no-renames --name-only"
N_ADD=$($DIFF --diff-filter=A | wc -l | tr -d ' ')
N_DEL=$($DIFF --diff-filter=D | wc -l | tr -d ' ')
# tr 把換行換成空格，sed 把結尾多出來的那個空格去掉
NAMES=$($DIFF --diff-filter=A | head -n 2 | tr '\n' ' ' | sed 's/ *$//')
COUNTS="+$N_ADD"
if [ "$N_DEL" -gt 0 ]; then
  COUNTS="$COUNTS -$N_DEL"
fi
git commit -q -m "$PREFIX: ${NAMES:-update} ($COUNTS)"

# push 可能失敗：另一個 workflow 剛好也推了東西上去。
# 失敗就把遠端的變更拉下來、把自己的 commit 重新疊在上面，再試一次。
# 因為 collect 動的是「今天」的檔案、compact 動的是「以前」的檔案，
# 兩邊碰到的路徑不重疊，所以 rebase 不會產生衝突。
for i in 1 2 3 4 5; do
  if git push origin HEAD:data; then
    echo "推送成功"
    exit 0
  fi
  echo "推送失敗（第 $i 次），拉取遠端後重試…"
  git fetch origin data
  if ! git rebase origin/data; then
    git rebase --abort || true
    echo "重疊失敗（罕見，通常代表兩邊改了同一個檔案）。這次放棄，下次排程會再試。"
    exit 1
  fi
  # 每次多等一點（2、4、6、8 秒），避免兩邊同時重試又撞在一起
  sleep $((i * 2))
done

echo "推送重試 5 次都失敗"
exit 1
