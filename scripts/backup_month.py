"""
每月備份：把某個月的日檔打包成一個 tar.gz，給 backup.yml 上傳到 GitHub Release。

┌─ 為什麼要備份到 Release ───────────────────────────────────────────┐
│ 1. 離線備份：萬一 data 分支被誤刪、被 force push 弄壞，還有一份。  │
│ 2. 讓 data 分支可以「瘦身」：分支的 git 歷史會一直長（每天 288 個   │
│    commit），每月備份完就可以把歷史清掉，只留目前的檔案。          │
│ 3. 想拿某個月的資料，下載一個檔就好，不用 clone 整個 repo。        │
└────────────────────────────────────────────────────────────────────┘

打包前的安全檢查（任何一項不過就中止，寧可不備份也不要備份不完整的東西）：
  - 只能備份「已經過完」的月份（這個月還在收，備份了也不完整）
  - 那個月不能還有「沒壓縮的小檔資料夾」—— 代表 compact 還沒做完
  - 每個 Parquet 都要讀得出 metadata（壞檔不要包進去）

用法：
    python scripts/backup_month.py --data-dir data-branch                 # 預設：上個月
    python scripts/backup_month.py --data-dir data-branch --month 2026-09 --out-dir out
"""

import argparse
import hashlib
import io
import os
import re
import sys
import tarfile
from datetime import datetime, timedelta, timezone

import pyarrow.parquet as pq

TAIPEI_TZ = timezone(timedelta(hours=8))
MONTH_RE = re.compile(r"^\d{4}-\d{2}$")

# 每份備份都附上的「基本資料」—— 單獨拿到一個月的備份也能知道站名、經緯度
ALWAYS_INCLUDE = ["stations.parquet", "weather_stations.parquet", "README.md"]
DATASET_DIRS = ["snapshots", "weather"]


def previous_month():
    """台北時間的上個月，例如今天 2026-11-02 -> '2026-10'。"""
    first_of_this_month = datetime.now(TAIPEI_TZ).replace(day=1)
    return (first_of_this_month - timedelta(days=1)).strftime("%Y-%m")


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def collect_files(data_dir, month):
    """
    找出要打包的檔案，回傳 (檔案清單, 問題清單)。
    檔案清單是相對於 data_dir 的路徑。
    """
    files, problems = [], []
    for sub in DATASET_DIRS:
        d = os.path.join(data_dir, sub)
        if not os.path.isdir(d):
            problems.append("找不到 {}/ 資料夾".format(sub))
            continue
        for name in sorted(os.listdir(d)):
            if not name.startswith(month + "-"):
                continue
            path = os.path.join(d, name)
            if os.path.isdir(path):
                # 小檔資料夾還在 = compact 還沒把這天壓完
                problems.append("{}/{}/ 還沒壓縮（compact 還沒做完這一天）".format(sub, name))
            elif name.endswith(".parquet"):
                files.append("{}/{}".format(sub, name))
    for name in ALWAYS_INCLUDE:
        if os.path.isfile(os.path.join(data_dir, name)):
            files.append(name)
    return files, problems


def date_ranges(dates):
    """['2026-09-01', '2026-09-02', '2026-09-05'] -> '09-01 ~ 09-02、09-05'（連續的日子合併）"""
    out, start, prev = [], None, None
    for d in sorted(dates) + [None]:
        cur = datetime.strptime(d, "%Y-%m-%d") if d else None
        if start and (cur is None or cur - prev != timedelta(days=1)):
            out.append(start.strftime("%m-%d") if start == prev else
                       "{:%m-%d} ~ {:%m-%d}".format(start, prev))
            start = None
        if cur and start is None:
            start = cur
        prev = cur
    return "、".join(out)


def build_manifest(data_dir, month, files):
    """產生清單（每個檔的列數、大小、SHA256），同時檢查每個 Parquet 都讀得出來。"""
    lines = ["# 資料備份 {}".format(month), "",
             "產生時間：{:%Y-%m-%d %H:%M}（台北）".format(datetime.now(TAIPEI_TZ)), ""]
    problems = []
    days = {sub: [] for sub in DATASET_DIRS}

    lines += ["| 檔案 | 列數 | 大小 | SHA256 |", "|---|---:|---:|---|"]
    for rel in files:
        path = os.path.join(data_dir, rel)
        rows = "—"
        if rel.endswith(".parquet"):
            try:
                rows = "{:,}".format(pq.read_metadata(path).num_rows)
            except Exception as e:   # noqa: BLE001 —— 任何讀取錯誤都代表這個檔有問題
                problems.append("{} 讀不出來：{}".format(rel, e))
        sub = rel.split("/")[0]
        if sub in days:
            days[sub].append(os.path.basename(rel)[:10])
        lines.append("| `{}` | {} | {:,} B | `{}` |".format(
            rel, rows, os.path.getsize(path), sha256_of(path)))

    # 列出這個月「沒有資料」的日子 —— 不算錯誤（收集剛開始的月份本來就不完整），
    # 但要寫清楚，免得之後以為備份漏了
    y, m = map(int, month.split("-"))
    first = datetime(y, m, 1)
    last = (first.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    all_days = [(first + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(last.day)]
    lines += [""]
    for sub in DATASET_DIRS:
        missing = [d for d in all_days if d not in days[sub]]
        lines.append("- `{}`：{} 天有資料{}".format(
            sub, len(days[sub]),
            "；沒有資料的日子：" + date_ranges(missing) if missing else "（整個月都有）"))
    return "\n".join(lines) + "\n", problems, days


def main():
    ap = argparse.ArgumentParser(description="把某個月的日檔打包成備份")
    ap.add_argument("--data-dir", required=True, help="data 分支展開的位置")
    ap.add_argument("--month", help="YYYY-MM，預設是上個月（台北時間）")
    ap.add_argument("--out-dir", default=".", help="備份檔放哪")
    args = ap.parse_args()

    month = args.month or previous_month()
    if not MONTH_RE.match(month):
        sys.exit("月份格式要是 YYYY-MM：{!r}".format(month))
    this_month = datetime.now(TAIPEI_TZ).strftime("%Y-%m")
    if month >= this_month:
        sys.exit("{} 還沒過完（現在是 {}），備份會不完整，中止".format(month, this_month))

    files, problems = collect_files(args.data_dir, month)
    if not any(f.split("/")[0] in DATASET_DIRS for f in files):
        problems.append("{} 完全沒有任何日檔".format(month))
    manifest, more, days = build_manifest(args.data_dir, month, files) if files else ("", [], {})
    problems += more
    if problems:
        print("❌ 備份中止，有 {} 個問題：".format(len(problems)))
        for p in problems:
            print("  - " + p)
        sys.exit(1)

    os.makedirs(args.out_dir, exist_ok=True)
    archive = os.path.join(args.out_dir, "data-{}.tar.gz".format(month))
    with tarfile.open(archive, "w:gz") as tar:
        for rel in files:
            tar.add(os.path.join(args.data_dir, rel), arcname=rel)
        # 清單也放一份在壓縮檔裡，下載下來就能核對
        data = manifest.encode("utf-8")
        info = tarfile.TarInfo("MANIFEST.md")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))

    manifest_path = os.path.join(args.out_dir, "MANIFEST-{}.md".format(month))
    with open(manifest_path, "w", encoding="utf-8") as f:
        f.write(manifest)

    digest = sha256_of(archive)
    with open(archive + ".sha256", "w", encoding="utf-8") as f:
        f.write("{}  {}\n".format(digest, os.path.basename(archive)))

    print("✅ {}：{} 個檔，{:,} bytes".format(archive, len(files), os.path.getsize(archive)))
    print("   SHA256 {}".format(digest))
    for sub, d in days.items():
        print("   {}：{} 天".format(sub, len(d)))

    # 交給 GitHub Actions 後面的步驟用
    gh_out = os.environ.get("GITHUB_OUTPUT")
    if gh_out:
        with open(gh_out, "a", encoding="utf-8") as f:
            f.write("month={}\narchive={}\nsha256={}\nmanifest={}\n".format(
                month, archive, digest, manifest_path))


if __name__ == "__main__":
    main()
