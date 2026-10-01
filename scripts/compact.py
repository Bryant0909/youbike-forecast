"""
phase 0-5／A：把小 Parquet 檔壓縮成「日檔」（YouBike 快照 + 天氣觀測）。

為什麼要做這件事？
    收集程式每 5 分鐘寫一個小檔，一天 288 個。三個月後就是 26000 個檔案 ——
    光是列出資料夾就會很慢，做分析時要一個一個讀更慢。
    而且 Parquet 的壓縮是「以檔案為單位」的：一天的資料放在一起壓，
    重複的值（相近的車輛數、同樣的時間戳）可以壓得非常好；切成 288 份就壓不動。

┌─ 最重要的設計原則：先確認新檔沒問題，才刪舊檔 ─────────────────────┐
│ 壓縮這個動作的本質是「刪掉原始資料」，一旦刪錯就再也回不來了       │
│ （YouBike 沒有歷史資料 API）。所以流程刻意拆成很保守的四步：       │
│   1. 讀進所有小檔，合併                                            │
│   2. 寫成日檔（先寫暫存檔再改名）                                  │
│   3. 【把日檔重新讀回來】，逐項核對筆數、欄位、時間點數量          │
│   4. 全部對得上，才刪掉小檔                                        │
│ 任何一項對不上就直接中止，小檔一個都不動。                         │
└────────────────────────────────────────────────────────────────────┘

只壓縮「已經結束的日子」—— 今天的資料還在持續寫入，壓了會漏掉後面的。

用法：
    python scripts/compact.py                      # 壓縮所有已結束但還沒壓的日子
    python scripts/compact.py --dry-run            # 只檢查不寫檔、不刪檔
    python scripts/compact.py --date 2026-09-30    # 只壓指定的某一天（可含今天，測試用）
    python scripts/compact.py --keep-small         # 壓完保留小檔（想自己比對時用）
"""

import argparse
import os
import re
import shutil
import sys
from datetime import datetime, timezone, timedelta

import pyarrow as pa
import pyarrow.parquet as pq

TAIPEI_TZ = timezone(timedelta(hours=8))

# 路徑一律從程式檔位置推算（原因見 collect.py 的註解）
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DATA_DIR = os.path.join(REPO_ROOT, "data", "raw")

DATE_DIR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# ---------------------------------------------------------------------------
# 要壓縮哪些資料集
# ---------------------------------------------------------------------------
#
# 現在有兩種資料要壓：YouBike 快照（每 5 分鐘）和天氣觀測（每 10 分鐘）。
# 它們的壓縮邏輯一模一樣，只有「欄位、時間欄位名稱、一天幾筆」不同，
# 所以把差異抽成下面這份規格，流程只寫一次。
#
# 【columns 必須跟對應的收集程式完全一致】
#   對不上就會中止 —— 這是刻意的。寧可壓縮失敗讓你發現，
#   也不要默默合併出一個欄位錯亂的檔案。
DATASETS = {
    "snapshots": {
        "label": "YouBike 快照",
        "time_col": "src_time",      # 這個欄位的「不同值數量」應該等於小檔數量
        "per_day": 288,              # 24 小時 ÷ 5 分鐘
        # 必須跟 collect.py 的 SNAPSHOT_SCHEMA 一致
        "columns": [
            "station_id", "is_active", "total_docks",
            "bikes_available", "docks_available", "src_time", "fetched_at",
        ],
    },
    "weather": {
        "label": "天氣觀測",
        "time_col": "obs_time",
        "per_day": 144,              # 24 小時 ÷ 10 分鐘（氣象署的更新頻率）
        # 必須跟 collect_weather.py 的 SNAPSHOT_SCHEMA 一致
        "columns": [
            "station_id", "temp_c", "humidity_pct", "pressure_hpa",
            "wind_speed_ms", "wind_dir_deg", "gust_ms", "precip_mm",
            "uv_index", "sunshine", "weather", "obs_time", "fetched_at",
        ],
    },
}


def log(msg):
    print("[{:%H:%M:%S}] {}".format(datetime.now(TAIPEI_TZ), msg), flush=True)


def rel(path):
    """把絕對路徑縮短成相對於專案根目錄的樣子，純粹是為了訊息好讀。"""
    try:
        return os.path.relpath(path, REPO_ROOT).replace("\\", "/")
    except ValueError:
        return path


def today_taipei():
    """今天是幾號（台北時間）。用來判斷哪些日子「已經結束」。"""
    return datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d")


def find_pending_days(snap_dir, only_date=None):
    """
    找出哪些日子需要壓縮。

    條件有兩個：
      1. 是一個 YYYY-MM-DD 的資料夾，裡面有 .parquet 小檔
      2. 日期比「今天」早 —— 今天的資料還在寫，不能壓

    會一次處理「所有」待壓縮的日子，而不是只處理昨天。
    為什麼？因為排程可能連續失敗好幾天，等你發現時已經積了一堆。
    每次都掃全部，就不需要人工補做。
    """
    if not os.path.isdir(snap_dir):
        return []

    today = today_taipei()
    days = []
    for name in sorted(os.listdir(snap_dir)):
        path = os.path.join(snap_dir, name)
        if not os.path.isdir(path) or not DATE_DIR_RE.match(name):
            continue
        if only_date and name != only_date:
            continue
        if not only_date and name >= today:
            log("跳過 {}：今天的資料還在收集中，等明天再壓".format(name))
            continue
        files = sorted(f for f in os.listdir(path) if f.endswith(".parquet"))
        if not files:
            continue
        days.append((name, path, files))
    return days


def compact_day(date_str, day_dir, filenames, snap_dir, spec, dry_run, keep_small):
    """壓縮某一天。回傳 True 代表成功。"""
    out_path = os.path.join(snap_dir, date_str + ".parquet")
    paths = [os.path.join(day_dir, f) for f in filenames]

    log("--- {}：{} 個小檔（一天理論上 {} 個，覆蓋率 {:.0f}%）".format(
        date_str, len(paths), spec["per_day"],
        100.0 * len(paths) / spec["per_day"]))

    # 【步驟 1】讀進所有小檔
    tables = []
    for p in paths:
        try:
            t = pq.read_table(p)
        except Exception as e:
            log("  中止：{} 讀不起來（{}: {}）".format(rel(p), type(e).__name__, e))
            log("  小檔一個都沒刪，請先處理這個壞檔再重跑")
            return False
        if t.column_names != spec["columns"]:
            log("  中止：{} 的欄位跟預期不符".format(rel(p)))
            log("    預期：{}".format(spec["columns"]))
            log("    實際：{}".format(t.column_names))
            return False
        tables.append(t)

    merged = pa.concat_tables(tables)
    rows_before = sum(t.num_rows for t in tables)

    # 日檔已經存在的兩種情況：
    #   a. 上次就壓好了（筆數夠）-> 不重壓，但小檔還在就繼續刪（補完上次沒做完的事）
    #   b. 上次壓完之後又收到更多小檔 -> 重壓一次把它們併進去
    if os.path.exists(out_path):
        existing = pq.read_table(out_path)
        if existing.num_rows >= rows_before:
            log("  日檔已存在且筆數足夠（{} 筆），不重壓".format(existing.num_rows))
            if keep_small:
                log("  --keep-small：保留 {} 個小檔".format(len(paths)))
            elif not dry_run:
                shutil.rmtree(day_dir)
                log("  已刪掉 {} 個小檔".format(len(paths)))
            return True
        log("  日檔已存在但筆數不足（{} < {}），重新壓一次".format(
            existing.num_rows, rows_before))

    # 【排序】按「站點 -> 時間」排。
    # 為什麼不是單純按時間排？因為之後做特徵工程時幾乎都是在看
    # 「同一個站的時間序列」（例如「30 分鐘前有幾台車」），
    # 同一站的資料排在一起讀起來快很多。
    # 附帶好處：同一站連續時間點的車輛數很接近，Parquet 壓縮這種
    # 連續相似值特別有效，檔案會更小。
    merged = merged.sort_by([("station_id", "ascending"),
                             (spec["time_col"], "ascending")])

    # 每個小檔裡的時間欄位都是同一個值（整份資料一起更新），
    # 所以「不同時間值的數量」應該剛好等於小檔的數量。
    # 這是一個很強的完整性檢查：對不上就代表有檔案內容不對。
    distinct_src = len(set(merged.column(spec["time_col"]).to_pylist()))
    if distinct_src != len(paths):
        log("  警告：{} 個小檔但只有 {} 個不同的時間點".format(len(paths), distinct_src))

    size_before = sum(os.path.getsize(p) for p in paths)

    if dry_run:
        log("  [dry-run] 會合併成 {} 筆（{} 個時間點），寫到 {}".format(
            rows_before, distinct_src, rel(out_path)))
        log("  [dry-run] 不寫檔、不刪檔")
        return True

    # 【步驟 2】寫日檔（先寫 .tmp 再改名，寫到一半被中斷也不會留下半個壞檔）
    tmp_path = out_path + ".tmp"
    # compression_level=9：日檔是長期保存用的，多花幾秒換更小的體積很值得
    pq.write_table(merged, tmp_path, compression="zstd", compression_level=9)
    os.replace(tmp_path, out_path)

    # 【步驟 3】把剛寫好的日檔重新讀回來核對。
    # 這一步是整個程式的重點：不相信「剛才寫檔應該沒問題」，而是真的讀回來驗。
    check = pq.read_table(out_path)
    problems = []
    if check.num_rows != rows_before:
        problems.append("筆數 {} != 小檔總和 {}".format(check.num_rows, rows_before))
    if check.column_names != spec["columns"]:
        problems.append("欄位不符：{}".format(check.column_names))
    check_distinct = len(set(check.column(spec["time_col"]).to_pylist()))
    if check_distinct != distinct_src:
        problems.append("時間點數量 {} != 合併前的 {}".format(check_distinct, distinct_src))

    if problems:
        log("  中止：日檔核對失敗，小檔一個都沒刪")
        for p in problems:
            log("    - " + p)
        log("  有問題的日檔留在 {}，請人工檢查".format(rel(out_path)))
        return False

    size_after = os.path.getsize(out_path)
    log("  核對通過：{} 筆、{} 個時間點、{} 欄".format(
        check.num_rows, check_distinct, check.num_columns))
    log("  {:.1f} KB -> {:.1f} KB（小 {:.1f} 倍）".format(
        size_before / 1024, size_after / 1024,
        size_before / size_after if size_after else 0))

    # 【步驟 4】確認無誤，才刪小檔
    if keep_small:
        log("  --keep-small：保留 {} 個小檔".format(len(paths)))
    else:
        shutil.rmtree(day_dir)
        log("  已刪掉 {} 個小檔".format(len(paths)))

    return True


def compact_dataset(name, spec, data_dir, only_date, dry_run, keep_small):
    """壓縮一個資料集（snapshots 或 weather）。回傳失敗的日期清單。"""
    root = os.path.join(data_dir, name)
    log("")
    log("### {}（{}/）".format(spec["label"], name))

    if not os.path.isdir(root):
        log("  沒有這個資料夾，跳過（還沒開始收集這種資料就會這樣）")
        return []

    days = find_pending_days(root, only_date)
    if not days:
        log("  沒有需要壓縮的日子（正常，代表該壓的都壓過了）")
        return []

    log("  要壓縮 {} 天：{}".format(len(days), ", ".join(d[0] for d in days)))

    failed = []
    for date_str, day_dir, filenames in days:
        if not compact_day(date_str, day_dir, filenames, root, spec,
                           dry_run, keep_small):
            failed.append("{}/{}".format(name, date_str))
    return failed


def main():
    ap = argparse.ArgumentParser(description="把小 Parquet 壓縮成日檔")
    ap.add_argument("--data-dir", default=DEFAULT_DATA_DIR,
                    help="資料資料夾，就是 collect.py 的 --out-dir 那一個（預設 data/raw）")
    ap.add_argument("--date", help="只壓指定的一天，格式 YYYY-MM-DD（可含今天，測試用）")
    ap.add_argument("--dataset", choices=sorted(DATASETS),
                    help="只壓某一種資料（預設兩種都壓）")
    ap.add_argument("--dry-run", action="store_true", help="只檢查，不寫檔也不刪檔")
    ap.add_argument("--keep-small", action="store_true", help="壓完保留小檔")
    args = ap.parse_args()

    log("資料資料夾：" + rel(args.data_dir))

    names = [args.dataset] if args.dataset else sorted(DATASETS)
    failed = []
    for name in names:
        # 一個資料集失敗不影響另一個 —— 天氣壓縮出問題不該擋住 YouBike 的壓縮
        failed += compact_dataset(name, DATASETS[name], args.data_dir,
                                  args.date, args.dry_run, args.keep_small)

    log("")
    if failed:
        log("有 {} 項壓縮失敗：{}".format(len(failed), ", ".join(failed)))
        return 1

    log("全部完成")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        # 失敗時用非 0 離開代碼，GitHub Actions 才會標成紅色失敗而不是靜靜吞掉
        log("執行失敗：{}: {}".format(type(e).__name__, e))
        sys.exit(1)
