"""
phase 0-6：每日資料覆蓋率檢查 —— 對付「靜默失敗」。

┌─ 這個程式要解決的問題 ─────────────────────────────────────────────┐
│ 最會殺死這類專案的不是「程式壞掉」，而是「程式壞掉了但你不知道」。 │
│ GitHub Actions 的排程會延遲、會跳過，API 也可能改格式或掛掉。      │
│ 這些都不會主動通知你 —— 你會照常過日子，兩個月後想開始建模，       │
│ 才發現資料只收到三個星期，而且傍晚尖峰時段幾乎是空的。            │
│                                                                    │
│ 所以每天檢查一次昨天收得怎麼樣，不夠就自動開 GitHub Issue          │
│ （Issue 會寄 email 給你），把「靜默失敗」變成「吵鬧的失敗」。      │
└────────────────────────────────────────────────────────────────────┘

為什麼不只看「收到幾筆」？
    因為筆數會騙人。一天收到 250 筆（覆蓋率 87%）聽起來還可以，
    但如果缺的那 38 筆全部集中在傍晚 17～20 點，對這個專案就是致命的 ——
    「借不到車」這件事最常發生的就是通勤尖峰，那段沒資料等於沒資料。
    所以這支程式同時看四件事：
      1. 樣本數（一天理論上 288 個時間點）
      2. 【最大連續空隙】—— 缺漏是分散的還是集中成一個大洞
      3. 【尖峰時段覆蓋率】—— 早上 7-10 點、傍晚 17-20 點單獨算
      4. 【資料新鮮度】—— 最新一筆資料距離現在多久（收集是不是已經停了）

用法：
    python scripts/check_coverage.py                      # 檢查昨天
    python scripts/check_coverage.py --date 2026-09-30    # 檢查指定某天
    python scripts/check_coverage.py --report-file r.md    # 把報告另存一份（給 Actions 開 Issue 用）
"""

import argparse
import math
import os
import re
import sys
from datetime import datetime, timezone, timedelta

import pyarrow.parquet as pq

TAIPEI_TZ = timezone(timedelta(hours=8))

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DATA_DIR = os.path.join(REPO_ROOT, "data", "raw")

DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})$")
SNAP_FILE_RE = re.compile(r"^(\d{8}T\d{6})\.parquet$")
DAY_FILE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.parquet$")

INTERVAL_MIN = 5                     # 預期的取樣間隔（分鐘）
EXPECTED_PER_DAY = 24 * 60 // INTERVAL_MIN   # = 288

# ---------------------------------------------------------------------------
# 警報門檻 —— 這些數字是可以調的，調的時候記得想「為什麼」
# ---------------------------------------------------------------------------

# 覆蓋率門檻。為什麼是 90% 而不是 99%？
# 因為 GitHub 的排程本來就會跳過幾次，要求太嚴會天天收到假警報 ——
# 而天天收到假警報的下一步就是你開始無視它，那監控就等於不存在了。
MIN_COVERAGE = 0.90

# 最大連續空隙門檻（分鐘）。超過 30 分鐘就代表不是「偶爾跳一次」，
# 而是真的斷過一段 —— 30 分鐘正好也是我們要預測的時間長度。
MAX_GAP_MINUTES = 30

# 尖峰時段（台北時間，左閉右開）。這是這個專案真正在乎的時段，
# 所以單獨算覆蓋率，門檻也訂得比整天嚴。
PEAK_WINDOWS = [("早上尖峰", 7, 10), ("傍晚尖峰", 17, 20)]
MIN_PEAK_COVERAGE = 0.90

# 最新一筆資料可以多舊。超過 2 小時代表收集很可能已經停了，
# 這是唯一一個「不用等到隔天也能發現」的檢查。
MAX_DATA_AGE_HOURS = 2

# 一份正常的資料應該有多少站。少太多代表 API 回傳的資料不完整。
MIN_STATIONS = 1000


def log(msg):
    print(msg, flush=True)


def yesterday_taipei():
    return (datetime.now(TAIPEI_TZ) - timedelta(days=1)).strftime("%Y-%m-%d")


def parse_snap_name(name):
    """從小檔檔名（20260930T094319.parquet）解析出台北時間。"""
    m = SNAP_FILE_RE.match(name)
    if not m:
        return None
    return datetime.strptime(m.group(1), "%Y%m%dT%H%M%S").replace(tzinfo=TAIPEI_TZ)


def load_day(snap_dir, date_str):
    """
    讀出某一天的資料，回傳 (每個時間點的台北時間清單, 總筆數, 站數, 延遲秒數清單)。

    要同時支援兩種形式，因為壓縮（phase 0-5）是每天凌晨才做的：
      - 已壓縮：snapshots/2026-09-30.parquet
      - 未壓縮：snapshots/2026-09-30/*.parquet（一堆小檔）
    """
    day_file = os.path.join(snap_dir, date_str + ".parquet")
    day_dir = os.path.join(snap_dir, date_str)

    paths = []
    if os.path.isfile(day_file):
        paths = [day_file]
        form = "已壓縮的日檔"
    elif os.path.isdir(day_dir):
        paths = [os.path.join(day_dir, f) for f in sorted(os.listdir(day_dir))
                 if f.endswith(".parquet")]
        form = "{} 個未壓縮的小檔".format(len(paths))
    if not paths:
        return None, 0, 0, [], "找不到資料"

    times = set()
    lags = []
    stations = set()
    rows = 0
    for p in paths:
        # 只讀需要的三個欄位。整天 52 萬列，全欄位讀進來很浪費。
        t = pq.read_table(p, columns=["src_time", "fetched_at", "station_id"])
        rows += t.num_rows
        src = t.column("src_time").to_pylist()
        fet = t.column("fetched_at").to_pylist()
        stations.update(t.column("station_id").to_pylist())
        # 同一個檔裡每列的 src_time 都一樣，所以用 set 收集不同的值就好
        for s, f in zip(src, fet):
            if s not in times:
                times.add(s)
                lags.append((f - s).total_seconds())

    local_times = sorted(s.astimezone(TAIPEI_TZ) for s in times)
    return local_times, rows, len(stations), lags, form


def fmt_clock(t, date_str):
    """
    把時間印成 HH:MM。跨到隔天午夜的話印成 24:00 而不是 00:00 ——
    「01:55 ~ 00:00」會讓人以為是往回跑，「01:55 ~ 24:00」一看就懂。
    """
    if t.strftime("%Y-%m-%d") != date_str and t.hour == 0 and t.minute == 0:
        return "24:00"
    return "{:%H:%M}".format(t)


def day_bounds(date_str):
    """
    這一天的「起點」和「該算到哪為止」，以及它是不是還沒過完。

    【為什麼需要這個】
    如果拿今天來檢查，一天當然還沒過完 —— 現在是早上 9 點的話，
    9 點到午夜當然沒有資料。若照整天 288 個時間點去算，就會報出
    「覆蓋率 37%」「空了 890 分鐘」「傍晚尖峰 0%」這種嚇人但毫無意義的數字。
    那不是缺漏，是未來還沒發生。

    所以檢查今天時，一律只算到「現在」為止。
    排程跑的是昨天（已經過完），走的是另一條路，行為完全不變。
    """
    day_start = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=TAIPEI_TZ)
    midnight = day_start + timedelta(days=1)
    now = datetime.now(TAIPEI_TZ)
    if now < midnight:
        # 這一天還沒過完（通常就是「今天」）
        return day_start, max(now, day_start), True
    return day_start, midnight, False


def expected_points(day_start, day_end):
    """
    從起點到終點之間理論上該有幾個取樣點。

    用【無條件進位】而不是無條件捨去，因為取樣點包含起點：
      07:00 ~ 09:14（134 分鐘）的取樣點是 07:00, 07:05, …, 09:10，共 27 個，
      而 134 // 5 = 26 會少算一個，算出來的覆蓋率就會超過 100%（看起來很蠢）。
    整天的情況兩種算法一樣（1440 / 5 = 288），所以不會影響排程跑的昨日報告。
    """
    minutes = (day_end - day_start).total_seconds() / 60.0
    return max(1, int(math.ceil(minutes / INTERVAL_MIN)))


def find_gaps(local_times, date_str):
    """
    找出這一天所有的「空隙」。

    空隙的定義：兩個相鄰時間點之間超過預期間隔（5 分鐘）的部分。
    一天的頭尾也算 —— 如果第一筆是早上 9 點，那 00:00~09:00 就是一個 9 小時的空隙，
    這種情況如果不算頭尾就會完全看不見。

    如果這一天還沒過完，結尾只算到「現在」（理由見 day_bounds）。
    """
    day_start, day_end, _ = day_bounds(date_str)

    marks = [day_start] + [t for t in local_times if t <= day_end] + [day_end]
    gaps = []
    for a, b in zip(marks, marks[1:]):
        minutes = (b - a).total_seconds() / 60.0
        # 只有「超過一個間隔」才算空隙。剛好 5 分鐘是正常的。
        if minutes > INTERVAL_MIN * 1.5:
            gaps.append((minutes, a, b))
    gaps.sort(reverse=True)
    return gaps


def peak_coverage(local_times, date_str):
    """
    分別算每個尖峰時段的覆蓋率。

    回傳每個時段的 (標籤, 起時, 迄時, 實得, 應得, 覆蓋率, 狀態)。
    狀態有三種：
      "done"     這個時段已經完整過完 —— 數字可以直接拿來判斷
      "partial"  正在進行中 —— 只跟「已經過的部分」比
      "future"   還沒到 —— 不算覆蓋率，也不該算成問題
    這三種分開，是為了不要把「還沒發生」誤報成「漏收」。
    """
    day_start, day_end, _ = day_bounds(date_str)
    out = []
    for label, h_from, h_to in PEAK_WINDOWS:
        w_start = day_start + timedelta(hours=h_from)
        w_end = day_start + timedelta(hours=h_to)
        effective_end = min(w_end, day_end)

        if effective_end <= w_start:
            out.append((label, h_from, h_to, 0, 0, 0.0, "future"))
            continue

        expected = expected_points(w_start, effective_end)
        got = sum(1 for t in local_times if w_start <= t < effective_end)
        state = "done" if effective_end >= w_end else "partial"
        out.append((label, h_from, h_to, got, expected,
                    got / expected if expected else 0.0, state))
    return out


def newest_sample(snap_dir):
    """
    整份資料裡最新的一筆是什麼時候。

    小檔的檔名就是它的時間，所以掃檔名就好，不用真的讀 parquet（快很多）。
    只有在「全部都壓縮過了、連今天的小檔都沒有」時才需要讀日檔。
    """
    if not os.path.isdir(snap_dir):
        return None
    newest = None
    for name in os.listdir(snap_dir):
        path = os.path.join(snap_dir, name)
        if os.path.isdir(path) and DATE_RE.match(name):
            for f in os.listdir(path):
                t = parse_snap_name(f)
                if t and (newest is None or t > newest):
                    newest = t
    if newest is not None:
        return newest

    # 沒有任何小檔 -> 找最新的日檔，讀出裡面最大的 src_time
    day_files = sorted(n for n in os.listdir(snap_dir) if DAY_FILE_RE.match(n))
    if not day_files:
        return None
    t = pq.read_table(os.path.join(snap_dir, day_files[-1]), columns=["src_time"])
    return max(t.column("src_time").to_pylist()).astimezone(TAIPEI_TZ)


def build_report(date_str, snap_dir, check_freshness):
    """
    做出報告，回傳 (problems, markdown)。

    problems 是「有問題的項目」清單。空的代表一切正常。
    markdown 是給人看的報告 —— 同一份內容會印在 Actions 的紀錄裡，
    也會在有問題時變成 GitHub Issue 的內容。
    """
    local_times, rows, n_stations, lags, form = load_day(snap_dir, date_str)
    problems = []
    lines = []

    lines.append("## {} 的資料收集狀況".format(date_str))
    lines.append("")

    if not local_times:
        problems.append("**完全沒有 {} 的資料** —— 收集程式很可能整天都沒跑成功".format(date_str))
        lines.append("找不到 `{}` 的任何資料。".format(date_str))
        lines.append("")
        lines.append("請檢查 [Actions 執行紀錄](../../actions/workflows/collect.yml)。")
        return problems, "\n".join(lines)

    day_start, day_end, partial = day_bounds(date_str)
    expected = expected_points(day_start, day_end)
    # 這一天還沒過完的話只算到「現在」為止（理由見 day_bounds）
    n = sum(1 for t in local_times if t <= day_end)
    coverage = n / expected

    if partial:
        lines.append("> ⏳ **{} 還沒過完**，以下只統計到 {:%H:%M} 為止（{} 個時間點）。"
                     .format(date_str, day_end, expected))
        lines.append("")

    # --- 1. 覆蓋率 ---
    ok = "✅" if coverage >= MIN_COVERAGE else "❌"
    lines.append("| 項目 | 數值 | 門檻 | |")
    lines.append("|---|---|---|---|")
    lines.append("| 時間點數 | {} / {}{} | — | |".format(
        n, expected, "（到目前為止）" if partial else ""))
    lines.append("| 覆蓋率 | **{:.1f}%** | ≥ {:.0f}% | {} |".format(
        coverage * 100, MIN_COVERAGE * 100, ok))
    if coverage < MIN_COVERAGE:
        problems.append("覆蓋率只有 {:.1f}%（門檻 {:.0f}%），{} 個時間點裡缺了 {} 個".format(
            coverage * 100, MIN_COVERAGE * 100, expected, expected - n))

    # --- 2. 最大連續空隙 ---
    gaps = find_gaps(local_times, date_str)
    max_gap = gaps[0][0] if gaps else 0.0
    ok = "✅" if max_gap <= MAX_GAP_MINUTES else "❌"
    lines.append("| 最大連續空隙 | **{:.0f} 分鐘** | ≤ {} 分鐘 | {} |".format(
        max_gap, MAX_GAP_MINUTES, ok))
    if max_gap > MAX_GAP_MINUTES:
        problems.append("有一段連續 {:.0f} 分鐘沒有資料（{} ~ {}），門檻是 {} 分鐘".format(
            max_gap, fmt_clock(gaps[0][1], date_str), fmt_clock(gaps[0][2], date_str),
            MAX_GAP_MINUTES))

    # --- 3. 站數 ---
    ok = "✅" if n_stations >= MIN_STATIONS else "❌"
    lines.append("| 站數 | {} | ≥ {} | {} |".format(n_stations, MIN_STATIONS, ok))
    if n_stations < MIN_STATIONS:
        problems.append("只有 {} 站（門檻 {} 站），API 回傳的資料可能不完整".format(
            n_stations, MIN_STATIONS))

    # --- 4. 資料新鮮度（最新一筆距現在多久）---
    #
    # 這一項跟其他四項不一樣：它問的是「現在」的狀態，不是「那一天」的狀態。
    # 所以只有在做例行的每日檢查（檢查昨天）時才算它是問題。
    # 如果你半年後回頭查 2026-09-20 收得怎麼樣，「收集現在有沒有在跑」
    # 跟那天的品質完全無關，算進去只會變成假警報。
    newest = newest_sample(snap_dir)
    if newest:
        age_h = (datetime.now(TAIPEI_TZ) - newest).total_seconds() / 3600.0
        too_old = age_h > MAX_DATA_AGE_HOURS
        if not check_freshness:
            mark = "—（回頭查舊資料，不列入判斷）"
        else:
            mark = "✅" if not too_old else "❌"
        lines.append("| 最新一筆資料 | {:%m-%d %H:%M}（{:.1f} 小時前） | ≤ {} 小時 | {} |".format(
            newest, age_h, MAX_DATA_AGE_HOURS, mark))
        if too_old and check_freshness:
            problems.append("最新一筆資料是 {:.1f} 小時前（{:%m-%d %H:%M}），收集很可能已經停了".format(
                age_h, newest))

    lines.append("")

    # --- 5. 尖峰時段（這個專案真正在乎的時段）---
    lines.append("### 尖峰時段（這個專案最在乎的時段）")
    lines.append("")
    lines.append("| 時段 | 時間點數 | 覆蓋率 | |")
    lines.append("|---|---|---|---|")
    for label, h_from, h_to, got, exp_pk, cov, state in peak_coverage(local_times, date_str):
        if state == "future":
            # 還沒到的時段不算覆蓋率，更不該算成問題 —— 它不是漏收，是還沒發生
            lines.append("| {}（{:02d}:00-{:02d}:00） | — | — | ⏳ 還沒到 |".format(
                label, h_from, h_to))
            continue
        note = "（進行中）" if state == "partial" else ""
        ok = "✅" if cov >= MIN_PEAK_COVERAGE else "❌"
        lines.append("| {}（{:02d}:00-{:02d}:00）{} | {} / {} | **{:.0f}%** | {} |".format(
            label, h_from, h_to, note, got, exp_pk, cov * 100, ok))
        if cov < MIN_PEAK_COVERAGE:
            problems.append("{}（{:02d}:00-{:02d}:00）{}覆蓋率只有 {:.0f}%，{} 個時間點裡缺了 {} 個".format(
                label, h_from, h_to, note, cov * 100, exp_pk, exp_pk - got))
    lines.append("")

    # --- 6. 其他資訊（不設門檻，只是給你參考）---
    lines.append("### 其他")
    lines.append("")
    lines.append("- 儲存形式：{}".format(form))
    lines.append("- 總筆數：{:,}（{} 個時間點 × 約 {} 站）".format(rows, n, n_stations))
    if lags:
        lines.append("- 資料延遲（我們抓到的時間 − 資料產生時間）："
                     "平均 {:.0f} 秒、最大 {:.0f} 秒".format(sum(lags) / len(lags), max(lags)))
    if len(gaps) > 1:
        lines.append("- 較大的空隙（前 5 名）：")
        for minutes, a, b in gaps[:5]:
            lines.append("  - {:.0f} 分鐘：{} ~ {}".format(
                minutes, fmt_clock(a, date_str), fmt_clock(b, date_str)))
    lines.append("")

    return problems, "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="檢查某一天的資料收集覆蓋率")
    ap.add_argument("--data-dir", default=DEFAULT_DATA_DIR,
                    help="資料資料夾（預設 data/raw）")
    ap.add_argument("--date", help="要檢查的日期 YYYY-MM-DD（預設是昨天）")
    ap.add_argument("--report-file", help="把報告另存成這個檔案（Actions 用它開 Issue）")
    args = ap.parse_args()

    date_str = args.date or yesterday_taipei()
    snap_dir = os.path.join(args.data_dir, "snapshots")

    # 「資料新鮮度」只有在檢查昨天/今天時才算是問題（理由見 build_report 裡的註解）
    check_freshness = date_str >= yesterday_taipei()
    problems, report = build_report(date_str, snap_dir, check_freshness)

    if problems:
        body = ["⚠️ **{} 的資料收集有問題**，共 {} 項：".format(date_str, len(problems)), ""]
        for p in problems:
            body.append("- " + p)
        body.append("")
        body.append(report)
        body.append("")          # Markdown 的 --- 前面一定要空行，不然上一行會變成大標題
        body.append("---")
        body.append("")
        body.append("這則通知由 `scripts/check_coverage.py` 自動產生。")
        body.append("排除問題後把這個 Issue 關掉就好；如果隔天還有問題會在同一個 Issue 下留言。")
        full = "\n".join(body)
    else:
        full = report + "\n一切正常，沒有需要處理的問題。\n"

    log(full)

    if args.report_file:
        with open(args.report_file, "w", encoding="utf-8", newline="\n") as f:
            f.write(full)

    # 把結果寫給 GitHub Actions，讓後面的步驟決定要不要開 Issue。
    # 寫進 $GITHUB_OUTPUT 這個檔案是 Actions 步驟之間傳值的標準做法。
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as f:
            f.write("status={}\n".format("alert" if problems else "ok"))
            f.write("date={}\n".format(date_str))
            f.write("n_problems={}\n".format(len(problems)))

    # 順手也寫進 Actions 的「執行摘要」，這樣即使一切正常，
    # 你點進去也看得到一份漂亮的報告，不用去翻 log。
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(full + "\n")

    # 【重要】就算有問題也回傳 0。
    # 為什麼？因為「覆蓋率不足」不是這支程式執行失敗 —— 它成功地發現了問題。
    # 如果回傳 1，Actions 會把這次執行標成紅色，後面「開 Issue」的步驟就不會跑，
    # 那反而收不到通知，本末倒置。真正的失敗（讀檔壞掉等）由最下面的 except 處理。
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        log("檢查程式本身執行失敗：{}: {}".format(type(e).__name__, e))
        sys.exit(1)
