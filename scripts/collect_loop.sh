#!/usr/bin/env bash
#
# 長時間執行的收集迴圈 —— 對付「GitHub 排程器很少叫醒我們」的解法。
#
# ┌─ 為什麼要這樣做 ───────────────────────────────────────────────────┐
# │ GitHub 的排程器對這個 repo 幾乎不運作（實測 7.5 小時只觸發 1 次，  │
# │ 應該要約 90 次）。原本的設計是「被叫醒 288 次，每次做 40 秒」，    │
# │ 但既然叫不醒，就反過來：「被叫醒 4~5 次，每次做 5.5 小時」。       │
# │                                                                    │
# │ 一次執行裡自己每 5 分鐘收一次，所以只要排程器一天能叫醒 4~5 次，   │
# │ 就有接近完整的覆蓋率 —— 這正好符合我們量到的觸發頻率。            │
# │                                                                    │
# │ 代價：GitHub 的 runner 會被長時間佔用（原設計一天約 3.2 小時）。    │
# │ 公開 repo 免費無上限，但這確實是比較重的用法。                     │
# │ 之後若改用外部服務定時觸發，就可以把這支換回單次執行。             │
# └────────────────────────────────────────────────────────────────────┘
#
# 用法：
#   bash scripts/collect_loop.sh <資料目錄> [持續分鐘數] [間隔秒數]
#
# 例：
#   bash scripts/collect_loop.sh "$GITHUB_WORKSPACE/data-branch" 330 300
#   bash scripts/collect_loop.sh ./data-branch 10 60      # 本機測試：跑 10 分鐘、每分鐘一次

# 【刻意不用 set -e】
#   set -e 會讓任何一個指令失敗就整支結束 —— 那正是這裡最不想要的行為。
#   網路抽一下、氣象署掛五分鐘、push 撞一次，都不該讓接下來 5 小時的收集停擺。
#   每一步都自己接住錯誤、印出來、繼續下一輪。
set -uo pipefail

DATA_DIR="${1:?用法: collect_loop.sh <資料目錄> [持續分鐘數] [間隔秒數]}"
DURATION_MIN="${2:-330}"    # 預設 330 分鐘 = 5.5 小時（GitHub 單次工作上限是 6 小時）
INTERVAL_SEC="${3:-300}"    # 預設 300 秒 = 5 分鐘

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
START=$(date +%s)
END=$(( START + DURATION_MIN * 60 ))

echo "收集迴圈開始：預計執行 ${DURATION_MIN} 分鐘，每 ${INTERVAL_SEC} 秒收一次"
echo "資料目錄：$DATA_DIR"
echo "預計結束：$(date -u -d "@$END" '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null || echo "$END")"
echo

round=0
ok_bike=0
ok_weather=0
ok_push=0

while :; do
  NOW=$(date +%s)
  # 如果剩下的時間不夠再做完一輪，就結束 —— 避免最後一輪做到一半被
  # job 逾時砍掉（那樣可能留下沒推上去的資料）。
  if [ $(( NOW + INTERVAL_SEC )) -gt "$END" ]; then
    echo "剩餘時間不足一輪，收工"
    break
  fi

  round=$(( round + 1 ))
  ROUND_START=$NOW
  echo "───── 第 $round 輪  $(date -u +%H:%M:%SZ) ─────"

  # 三個步驟各自獨立：任何一個失敗都只印訊息，不影響其他兩個，也不中斷迴圈。
  if python "$REPO_ROOT/scripts/collect.py" --out-dir "$DATA_DIR"; then
    ok_bike=$(( ok_bike + 1 ))
  else
    echo "!! YouBike 收集這一輪失敗，繼續下一輪"
  fi

  # 天氣失敗不能影響 YouBike（天氣是附加資料，YouBike 才是主角）
  if python "$REPO_ROOT/scripts/collect_weather.py" --out-dir "$DATA_DIR"; then
    ok_weather=$(( ok_weather + 1 ))
  else
    echo "!! 天氣收集這一輪失敗，繼續下一輪"
  fi

  # 每一輪都推上去，而不是最後一次推。
  # 為什麼？因為資料只存在 runner 的硬碟上，job 一旦被砍（逾時、GitHub 維護、
  # 取消）那些還沒推的資料就全沒了。每輪推一次的話，最多只損失這 5 分鐘。
  if bash "$REPO_ROOT/scripts/push_data.sh" data "$DATA_DIR"; then
    ok_push=$(( ok_push + 1 ))
  else
    echo "!! 推送這一輪失敗，資料留在本地，下一輪會一起推上去"
  fi

  # 【睡到下一個間隔，而不是固定睡 300 秒】
  # 每一輪本身要花 3~10 秒，固定睡 300 秒的話取樣時間會一輪比一輪晚，
  # 五個小時下來會漂移好幾分鐘。扣掉已經花掉的時間可以讓取樣間隔保持穩定。
  ELAPSED=$(( $(date +%s) - ROUND_START ))
  SLEEP=$(( INTERVAL_SEC - ELAPSED ))
  if [ "$SLEEP" -gt 0 ]; then
    sleep "$SLEEP"
  else
    echo "（這一輪花了 ${ELAPSED} 秒，超過間隔，不睡直接進下一輪）"
  fi
done

echo
echo "───── 收集迴圈結束 ─────"
echo "共 $round 輪：YouBike 成功 $ok_bike、天氣成功 $ok_weather、推送成功 $ok_push"
echo "（天氣每 10 分鐘才更新，所以約一半的輪次會印「跳過」，那不算失敗）"
