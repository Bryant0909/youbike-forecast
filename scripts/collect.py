"""
phase 0-3：YouBike 即時資料收集程式。

每執行一次，就抓一次臺北市 YouBike 2.0 的即時站點資料，存成一個 Parquet 檔。
之後會由 GitHub Actions 每 5 分鐘自動執行一次。

設計上的三個重點（為什麼這樣寫，見 docs/DECISIONS.md）：

1. 【靜態 / 動態分開存】
   API 回傳的 18 個欄位裡，有一半是永遠不變的（站名、地址、經緯度…）。
   每 5 分鐘把 1808 個站名重複存一次是純粹的浪費，所以：
     - 會變的欄位   -> 每次存一個小快照檔
     - 不會變的欄位 -> 只存在 data/raw/stations.parquet，有變動時才更新
   實測可以省下約 62% 的空間。

2. 【用來源時間當檔名，天然去重】
   API 的 mday 欄位是「整份資料的產生時間」（全部站點都一樣）。
   我們直接拿它當檔名，所以如果來源還沒更新、我們抓到同一份資料，
   檔名會撞在一起 —— 程式就知道這是重複的，直接跳過不存。
   這樣不需要額外記錄「上次抓到什麼」，也不會在訓練資料裡混進假的重複觀測。

3. 【只新增、不修改】
   每次都寫一個全新的小檔，絕不去改已經存在的檔案。
   這樣任何一次執行失敗，最多只損失那 5 分鐘的資料，動不到其他任何東西。

用法：
    python scripts/collect.py              # 正常抓一次並存檔
    python scripts/collect.py --dry-run    # 只抓不存，用來測試
"""

import argparse
import os
import sys
import time
from datetime import datetime, timezone, timedelta

import requests
import pyarrow as pa
import pyarrow.parquet as pq

# ---------------------------------------------------------------------------
# 設定
# ---------------------------------------------------------------------------

# 臺北市交通局實際存放即時資料的網址。
# 為什麼不用 data.taipei 的 API？因為那支有分頁（一次最多 1000 筆），
# 1808 個站要抓兩次才抓得完，每 5 分鐘做這件事只是自找麻煩。
# 這支是純靜態 JSON，不用申請 key、不用分頁、速度快。
SOURCE_URL = (
    "https://tcgbusfs.blob.core.windows.net/dotapp/youbike/v2/youbike_immediate.json"
)

TAIPEI_TZ = timezone(timedelta(hours=8))  # 台北時間 = UTC+8

# 抓取失敗時重試的間隔（秒）。網路偶爾抽風很正常，重試比直接放棄好。
RETRY_WAITS = [2, 5, 10]

# 合理性檢查：正常應該有 1800 站左右。如果只抓到 500 站，
# 代表來源出問題了，這種半殘的資料存下去比沒存更糟（之後很難發現）。
MIN_EXPECTED_STATIONS = 1000

# API 原始欄位名稱 -> 我們自己用的名稱。
# 原始名稱不太好懂（sno？act？Quantity？），統一改成看得懂的名字。
DYNAMIC_MAP = {
    "sno": "station_id",                          # 站點編號
    "act": "is_active",                           # 1=營運中, 0=停用
    "Quantity": "total_docks",                    # 總車柱數
    "available_rent_bikes": "bikes_available",    # 可借車輛數 <- 這就是要預測的東西
    "available_return_bikes": "docks_available",  # 可還空位數
}

STATIC_MAP = {
    "sno": "station_id",
    "sna": "name",
    "snaen": "name_en",
    "sarea": "district",
    "sareaen": "district_en",
    "ar": "address",
    "aren": "address_en",
    "latitude": "lat",
    "longitude": "lon",
}

# 每個快照檔的欄位格式。明確寫死型別有兩個好處：
#   1. 檔案更小（int16 只佔 2 bytes，Python 的 int 佔 28 bytes）
#   2. 之後每個檔的格式都一模一樣，合併時不會出錯
SNAPSHOT_SCHEMA = pa.schema([
    ("station_id", pa.string()),
    ("is_active", pa.int8()),
    ("total_docks", pa.int16()),
    ("bikes_available", pa.int16()),
    ("docks_available", pa.int16()),
    # src_time：資料本身的產生時間（來自 API 的 mday）
    ("src_time", pa.timestamp("s", tz="UTC")),
    # fetched_at：我們發出請求的時間。
    # 兩個都要存，才能知道「資料有多舊」—— 這兩個時間差通常有 1~3 分鐘。
    ("fetched_at", pa.timestamp("s", tz="UTC")),
])

STATIONS_SCHEMA = pa.schema([
    ("station_id", pa.string()),
    ("name", pa.string()),
    ("name_en", pa.string()),
    ("district", pa.string()),
    ("district_en", pa.string()),
    ("address", pa.string()),
    ("address_en", pa.string()),
    ("lat", pa.float64()),
    ("lon", pa.float64()),
    ("first_seen", pa.timestamp("s", tz="UTC")),  # 第一次看到這個站
    ("last_seen", pa.timestamp("s", tz="UTC")),   # 最後一次看到（站被撤掉就會停住）
])


# ---------------------------------------------------------------------------
# 工具函式
# ---------------------------------------------------------------------------

def log(msg):
    """統一的訊息輸出格式，前面加上時間，方便之後在 Actions 的紀錄裡對時間。"""
    print("[{:%H:%M:%S}] {}".format(datetime.now(TAIPEI_TZ), msg), flush=True)


def parse_taipei_time(s):
    """
    把 API 的時間字串（例如 '2026-09-29 16:59:03'）轉成有時區的 datetime。

    API 給的時間沒有標時區，但它是台北時間。我們一律轉成 UTC 再存，
    因為 UTC 沒有日光節約時間之類的模糊地帶，之後做時間計算不會出錯。
    要看台北時間時再轉回來就好。
    """
    naive = datetime.strptime(s.strip(), "%Y-%m-%d %H:%M:%S")
    return naive.replace(tzinfo=TAIPEI_TZ).astimezone(timezone.utc)


def fetch_with_retry():
    """抓取 API 資料，失敗會重試。全部失敗就丟出例外。"""
    last_err = None
    for attempt in range(len(RETRY_WAITS) + 1):
        try:
            resp = requests.get(SOURCE_URL, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            if not isinstance(data, list):
                raise ValueError("預期是 list，實際拿到 " + type(data).__name__)
            return data
        except Exception as e:
            last_err = e
            if attempt < len(RETRY_WAITS):
                wait = RETRY_WAITS[attempt]
                log("第 {} 次嘗試失敗（{}），{} 秒後重試…".format(
                    attempt + 1, type(e).__name__, wait))
                time.sleep(wait)
    raise RuntimeError("重試 {} 次都失敗：{}".format(len(RETRY_WAITS) + 1, last_err))


def validate(records):
    """
    存檔前的品質檢查。寧可這次不存，也不要存壞資料進去。

    壞資料最可怕的地方在於「安靜」—— 它不會報錯，但會在三個月後
    毀掉你的模型，而你完全不知道是哪裡出問題。
    """
    if len(records) < MIN_EXPECTED_STATIONS:
        raise ValueError(
            "只拿到 {} 站，少於預期的 {} 站，來源可能出問題了，這次不存".format(
                len(records), MIN_EXPECTED_STATIONS))
    missing = [k for k in DYNAMIC_MAP if k not in records[0]]
    if missing:
        raise ValueError("資料少了必要欄位：{}（API 格式可能改了）".format(missing))


def atomic_write_parquet(table, path):
    """
    安全地寫檔：先寫成暫存檔，寫完才改名成正式檔名。

    為什麼要這樣？因為如果直接寫正式檔名，萬一寫到一半程式被中斷
    （Actions 逾時、斷電…），就會留下一個「半個」的壞檔案，
    而且看起來跟好檔案一模一樣。改名這個動作是瞬間完成的，
    所以檔案要嘛不存在，要嘛就是完整的。
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    # zstd 壓縮率比預設的 snappy 好不少，讀取速度差異可以忽略
    pq.write_table(table, tmp, compression="zstd")
    os.replace(tmp, path)  # 同一個磁碟上的改名是原子操作


# ---------------------------------------------------------------------------
# 主要流程
# ---------------------------------------------------------------------------

def build_snapshot(records, src_time, fetched_at):
    """把 API 回傳的資料整理成一張「只有會變的欄位」的表。"""
    cols = {new: [] for new in DYNAMIC_MAP.values()}
    for r in records:
        for old, new in DYNAMIC_MAP.items():
            v = r.get(old)
            if new == "station_id":
                v = str(v)
            else:
                # API 有時把數字給成字串（例如 act 是 "1"），統一轉成整數
                v = int(v) if v is not None else 0
            cols[new].append(v)

    n = len(records)
    cols["src_time"] = [src_time] * n
    cols["fetched_at"] = [fetched_at] * n
    # 這兩欄每一列的值都一樣，看起來很浪費，但 Parquet 會自動把重複值壓成一份，
    # 實際上幾乎不佔空間。好處是每個檔案自己就看得懂，不用去查檔名。
    return pa.Table.from_pydict(cols, schema=SNAPSHOT_SCHEMA)


def update_stations(records, now, path, dry_run):
    """
    更新站點基本資料（站名、地址、經緯度…）。

    這些資料幾乎不會變，所以【只有真的變了才寫檔】。
    這很重要：如果每 5 分鐘都改寫這個檔，git 每天會多 288 個無意義的變更紀錄，
    而且改寫檔案本身就有寫壞的風險。
    """
    existing = {}
    if os.path.exists(path):
        for r in pq.read_table(path).to_pylist():
            existing[r["station_id"]] = r

    changed = 0
    for r in records:
        sid = str(r["sno"])
        new_row = {}
        for old, new in STATIC_MAP.items():
            new_row[new] = r.get(old)
        new_row["station_id"] = sid
        new_row["lat"] = float(new_row["lat"])
        new_row["lon"] = float(new_row["lon"])

        prev = existing.get(sid)
        if prev is None:
            # 新站（或第一次執行）
            new_row["first_seen"] = now
            new_row["last_seen"] = now
            existing[sid] = new_row
            changed += 1
        else:
            # 檢查基本資料有沒有變（例如站名改了、站點搬家）
            diff = any(prev.get(k) != new_row[k] for k in STATIC_MAP.values())
            prev.update(new_row)
            prev["last_seen"] = now
            if diff:
                changed += 1

    if changed == 0 and os.path.exists(path):
        return "沒變動，不寫檔"

    if dry_run:
        return "[dry-run] 會更新 {} 站（共 {} 站）".format(changed, len(existing))

    rows = sorted(existing.values(), key=lambda x: x["station_id"])
    table = pa.Table.from_pylist(rows, schema=STATIONS_SCHEMA)
    atomic_write_parquet(table, path)
    return "更新了 {} 站（共 {} 站）".format(changed, len(existing))


def main():
    ap = argparse.ArgumentParser(description="抓一次 YouBike 即時資料並存成 Parquet")
    ap.add_argument("--out-dir", default="data/raw", help="資料存放的資料夾")
    ap.add_argument("--dry-run", action="store_true", help="只抓資料不存檔，用來測試")
    args = ap.parse_args()

    fetched_at = datetime.now(timezone.utc).replace(microsecond=0)
    log("開始抓取：" + SOURCE_URL)

    records = fetch_with_retry()
    log("拿到 {} 站".format(len(records)))

    validate(records)

    # 取得這份資料的「來源時間」。理論上每一站的 mday 都一樣（整份檔案一起更新），
    # 但為了保險，取最大值（最新的那個）。
    src_time = max(parse_taipei_time(r["mday"]) for r in records if r.get("mday"))
    src_local = src_time.astimezone(TAIPEI_TZ)
    lag = (fetched_at - src_time).total_seconds()
    log("資料產生時間：{:%Y-%m-%d %H:%M:%S}（台北），比現在舊 {:.0f} 秒".format(src_local, lag))

    # 檔名用「來源時間」而不是「抓取時間」—— 這就是去重的關鍵。
    # 同一份資料不管被抓幾次，都會對應到同一個檔名。
    snap_path = os.path.join(
        args.out_dir, "snapshots",
        "{:%Y-%m-%d}".format(src_local),
        "{:%Y%m%dT%H%M%S}.parquet".format(src_local),
    )

    if os.path.exists(snap_path):
        log("跳過：這份資料已經存過了（{}）".format(os.path.basename(snap_path)))
        log("（代表來源還沒產生新資料，不是錯誤）")
        return 0

    table = build_snapshot(records, src_time, fetched_at)

    if args.dry_run:
        log("[dry-run] 不寫檔。表格大小：{} 列 x {} 欄".format(
            table.num_rows, table.num_columns))
        log("[dry-run] 本來會寫到：" + snap_path)
        log("[dry-run] 前 3 列：")
        for row in table.to_pylist()[:3]:
            log("    " + str(row))
    else:
        atomic_write_parquet(table, snap_path)
        size_kb = os.path.getsize(snap_path) / 1024
        log("已存檔：{}（{:.1f} KB）".format(snap_path, size_kb))

    stations_path = os.path.join(args.out_dir, "stations.parquet")
    log("站點基本資料：" + update_stations(records, fetched_at, stations_path, args.dry_run))

    # 順手印一個現況摘要，這樣在 Actions 的紀錄裡一眼就能看出資料正不正常
    active = [r for r in records if str(r.get("act")) == "1"]
    empty = sum(1 for r in active if r["available_rent_bikes"] == 0)
    full = sum(1 for r in active if r["available_return_bikes"] == 0)
    log("現況：營運中 {} 站，借不到車 {} 站，還不了車 {} 站".format(len(active), empty, full))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        # 失敗時用非 0 的離開代碼，這樣 GitHub Actions 才會把這次執行標成紅色失敗，
        # 不然錯誤會被靜靜吞掉，等你發現時已經斷了兩星期。
        log("執行失敗：{}: {}".format(type(e).__name__, e))
        sys.exit(1)
