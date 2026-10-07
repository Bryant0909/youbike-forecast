"""
phase 1-1：資料載入層 —— 把散落的 Parquet 檔讀成一張可以直接分析的表。

┌─ 為什麼要有這一層，而不是每次在 notebook 裡自己讀檔 ───────────────┐
│ 因為「讀資料」這件事在這個專案裡意外地麻煩，而且每個 phase 都要做： │
│                                                                    │
│ 1. 資料有兩種形式 —— 今天的是一堆 5 分鐘小檔，之前的是壓縮好的日檔 │
│    （phase 0-5 每天凌晨才壓）。分析時你不該關心這件事。            │
│ 2. 資料可能在兩個地方 —— 正式資料在 data-branch/（data 分支），    │
│    本機測試時在 data/raw/。                                        │
│ 3. 行政區、站名、經緯度存在另一個檔（stations.parquet），          │
│    想「只看大安區」就得先 join。                                   │
│ 4. 時間存的是 UTC，但人看的是台北時間，而且幾乎每個分析都要        │
│    「幾點」「星期幾」「是不是週末」這些欄位。                      │
│                                                                    │
│ 這些邏輯寫一次放在這裡，notebook 就只要一行 load_snapshots()。      │
│ 如果每個 notebook 各自實作一次，遲早會出現「兩個 notebook 算出      │
│ 不一樣的結果」而你找不出為什麼。                                    │
└────────────────────────────────────────────────────────────────────┘

用法（在 notebook 或其他程式裡）：

    from youbike.data import load_snapshots, load_stations, summary

    # 先看看手上有什麼資料
    summary()

    # 讀全部資料
    df = load_snapshots()

    # 只讀大安區、只讀某段日期
    df = load_snapshots(start="2026-10-01", end="2026-10-31", district="大安區")

也可以直接當程式跑，會印出資料盤點：

    python -m youbike.data
"""

import os
import re
from datetime import datetime, timezone, timedelta

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

TAIPEI_TZ = timezone(timedelta(hours=8))

# 從這個檔案往上三層就是專案根目錄（src/youbike/data.py -> src/youbike -> src -> 根）
# 跟 scripts/ 裡的程式一樣，不依賴「執行時在哪個資料夾」。
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
DAY_FILE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.parquet$")

INTERVAL_MIN = 5
EXPECTED_PER_DAY = 24 * 60 // INTERVAL_MIN   # = 288


class DataNotFound(Exception):
    """找不到資料時丟這個，訊息裡會直接告訴你該打哪個指令。"""


# ---------------------------------------------------------------------------
# 找資料在哪
# ---------------------------------------------------------------------------

def default_data_dir():
    """
    自動找資料在哪，優先順序：

      1. 環境變數 `YOUBIKE_DATA_DIR` —— 想讀別的位置（例如備份）時用
      2. `<專案>/data-branch/`      —— data 分支用 git worktree 展開的位置（正式資料）
      3. `<專案>/data/raw/`         —— 本機直接跑 collect.py 時的預設位置

    找不到就丟 DataNotFound，訊息裡附上還原資料的指令 ——
    換電腦後最容易卡住的就是這一步，錯誤訊息直接給答案比讓你去翻文件好。
    """
    env = os.environ.get("YOUBIKE_DATA_DIR")
    if env:
        if os.path.isdir(os.path.join(env, "snapshots")):
            return env
        raise DataNotFound(
            "環境變數 YOUBIKE_DATA_DIR 指向 {}，但裡面找不到 snapshots/ 資料夾".format(env))

    for candidate in (os.path.join(REPO_ROOT, "data-branch"),
                      os.path.join(REPO_ROOT, "data", "raw")):
        if os.path.isdir(os.path.join(candidate, "snapshots")):
            return candidate

    raise DataNotFound(
        "找不到任何資料。正式資料存在 repo 的 data 分支，用這個指令展開：\n"
        "    git worktree add data-branch data\n"
        "（如果只是想在本機測試，先跑一次 python scripts/collect.py 也會產生 data/raw/）"
    )


def _snap_dir(data_dir=None, subdir="snapshots"):
    # subdir：YouBike 快照在 snapshots/，天氣觀測在 weather/，兩邊的檔案結構一模一樣
    return os.path.join(data_dir or default_data_dir(), subdir)


# ---------------------------------------------------------------------------
# 盤點：手上有哪些日子的資料
# ---------------------------------------------------------------------------

def available_days(data_dir=None, subdir="snapshots"):
    """
    列出有資料的每一天。

    subdir 預設是 YouBike 快照；給 "weather" 就是天氣觀測（youbike.weather 用的）。
    兩種資料共用這一份掃描邏輯，不各寫一份 —— 下面那個「邊掃邊塞會漏資料」的
    bug 修一次就兩邊都修好。

    回傳 list of dict：
        {"date": "2026-09-30", "form": "小檔" or "日檔", "paths": [...], "n_files": N}

    「兩種形式」是 phase 0-5 壓縮造成的：當天的還是一堆小檔，
    之前的已經壓成一個日檔。分析的人不該需要知道這件事，所以在這裡吸收掉。
    """
    snap_dir = _snap_dir(data_dir, subdir)

    # 【刻意分兩個 dict 再合併，而不是邊掃邊塞進同一個 dict】
    #
    # 為什麼？因為同一天可能「日檔」和「小檔資料夾」同時存在
    # （壓縮做到一半中斷，或壓縮之後又收到新的小檔）。
    # os.listdir() 排序後，'2026-09-27'（資料夾）會排在 '2026-09-27.parquet'
    # （檔案）前面 —— 如果邊掃邊塞，後來的那筆就會把前面那筆整個覆蓋掉，
    # 於是有一半的資料被無聲無息地丟掉。
    # 分開收集再合併就跟掃描順序完全無關，不可能出現這種錯。
    day_files = {}   # 日期 -> 壓縮好的日檔路徑
    dir_files = {}   # 日期 -> 該天的小檔路徑清單

    for name in sorted(os.listdir(snap_dir)):
        path = os.path.join(snap_dir, name)

        m = DAY_FILE_RE.match(name)
        if m and os.path.isfile(path):
            day_files[m.group(1)] = path
            continue

        if os.path.isdir(path) and DATE_RE.match(name):
            files = sorted(os.path.join(path, f) for f in os.listdir(path)
                           if f.endswith(".parquet"))
            if files:
                dir_files[name] = files

    out = []
    for date in sorted(set(day_files) | set(dir_files)):
        paths, forms = [], []
        if date in day_files:
            paths.append(day_files[date])
            forms.append("日檔")
        if date in dir_files:
            paths += dir_files[date]
            forms.append("小檔")
        out.append({"date": date, "form": " + ".join(forms),
                    "paths": paths, "n_files": len(paths)})
    return out


# ---------------------------------------------------------------------------
# 站點基本資料
# ---------------------------------------------------------------------------

def load_stations(data_dir=None):
    """
    讀站點基本資料（站名、行政區、地址、經緯度），回傳 pandas DataFrame。

    這個檔幾乎不變，collect.py 只有在站點資料真的有變動時才會改寫它。
    """
    import pandas as pd  # noqa: F401  （在函式內 import，讓不需要 pandas 的呼叫者不必裝）

    path = os.path.join(data_dir or default_data_dir(), "stations.parquet")
    if not os.path.isfile(path):
        raise DataNotFound("找不到站點基本資料：{}".format(path))
    return pq.read_table(path).to_pandas()


def district_station_ids(district, data_dir=None):
    """某個行政區（例如 '大安區'）有哪些站的編號。"""
    st = load_stations(data_dir)
    ids = st.loc[st["district"] == district, "station_id"].tolist()
    if not ids:
        available = sorted(st["district"].dropna().unique())
        raise ValueError("找不到行政區 {!r}。可用的有：{}".format(district, available))
    return ids


# ---------------------------------------------------------------------------
# 主角：讀快照資料
# ---------------------------------------------------------------------------

def load_snapshots(start=None, end=None, district=None, station_ids=None,
                   include_inactive=False, with_station_info=False,
                   data_dir=None, verbose=True):
    """
    讀出指定範圍的快照資料，回傳 pandas DataFrame。

    參數：
        start, end        日期字串 'YYYY-MM-DD'，含頭含尾。None = 不限制
        district          只要某個行政區，例如 '大安區'
        station_ids       只要指定的站（給了 district 就不用給這個）
        include_inactive  要不要包含停用中的站（預設不要）
        with_station_info 要不要把站名/行政區/經緯度也 join 進來
        verbose           要不要印進度（讀很多天時會跑一陣子）

    回傳的欄位：
        station_id        站點編號
        src_time          資料產生時間（UTC，有時區）<- 做時間計算請用這個
        time_taipei       同一個時間的台北時間（給人看的）
        date              台北時間的日期（字串，方便 groupby）
        hour, minute      台北時間的時、分
        weekday           星期幾（0=週一 ... 6=週日）
        is_weekend        是不是週末
        is_active         1=營運中
        total_docks       總車柱數
        bikes_available   可借車輛數  <- 要預測的東西
        docks_available   可還空位數
        fetched_at        我們抓到的時間（UTC）
        lag_seconds       資料有多舊（fetched_at - src_time，秒）

    【為什麼一天一天讀、讀完才合併】
    一天約 52 萬列。如果一次把 30 天全部讀進記憶體再篩選，尖峰會用掉好幾 GB；
    改成「讀一天 -> 立刻篩掉不要的站 -> 留著」，記憶體峰值只有一天的量。
    在筆電上跑分析時這個差別很有感。
    """
    import pandas as pd

    data_dir = data_dir or default_data_dir()

    if district is not None:
        if station_ids is not None:
            raise ValueError("district 和 station_ids 只能給一個")
        station_ids = district_station_ids(district, data_dir)

    id_set = pa.array(station_ids, type=pa.string()) if station_ids else None

    days = available_days(data_dir)
    if start:
        days = [d for d in days if d["date"] >= start]
    if end:
        days = [d for d in days if d["date"] <= end]

    if not days:
        raise DataNotFound(
            "指定範圍（start={}, end={}）內沒有任何資料。"
            "有資料的日子：{}".format(
                start, end, [d["date"] for d in available_days(data_dir)]))

    tables = []
    for i, day in enumerate(days, 1):
        if verbose:
            print("讀取 {} （{}，{} 個檔）… {}/{}".format(
                day["date"], day["form"], day["n_files"], i, len(days)), flush=True)
        for path in day["paths"]:
            t = pq.read_table(path)
            if id_set is not None:
                t = t.filter(pc.is_in(t.column("station_id"), value_set=id_set))
            if not include_inactive:
                t = t.filter(pc.equal(t.column("is_active"), 1))
            if t.num_rows:
                tables.append(t)

    if not tables:
        raise DataNotFound("篩選之後一列都不剩，請檢查 district / station_ids 是不是打錯了")

    df = pa.concat_tables(tables).to_pandas()

    # 同一個 (站, 時間) 只該出現一次。會重複通常是壓縮做到一半中斷、
    # 日檔和小檔同時存在造成的 —— 不吭一聲地留著會讓之後的統計全部偏掉。
    dup = df.duplicated(subset=["station_id", "src_time"], keep="first")
    if dup.any():
        if verbose:
            print("注意：有 {} 列重複的 (站, 時間)，已保留第一筆".format(int(dup.sum())))
        df = df[~dup]

    # --- 加上時間相關欄位 ---
    # 每個分析都需要「幾點」「星期幾」，在這裡一次算好，
    # 免得每個 notebook 各自算一次（還可能算得不一樣）。
    local = df["src_time"].dt.tz_convert(TAIPEI_TZ)
    df["time_taipei"] = local
    df["date"] = local.dt.strftime("%Y-%m-%d")
    df["hour"] = local.dt.hour.astype("int8")
    df["minute"] = local.dt.minute.astype("int8")
    df["weekday"] = local.dt.weekday.astype("int8")
    df["is_weekend"] = df["weekday"] >= 5
    df["lag_seconds"] = (df["fetched_at"] - df["src_time"]).dt.total_seconds().astype("int32")

    if with_station_info:
        # 只取分析真正會用到的欄位。
        # 注意 total_docks 不在這裡 —— 它是「會變的」欄位（車柱可能增減），
        # 所以存在快照表裡而不是站點基本資料表裡（見 collect.py 的 STATIONS_SCHEMA）。
        st = load_stations(data_dir)[
            ["station_id", "name", "district", "address", "lat", "lon"]
        ]
        df = df.merge(st, on="station_id", how="left")
        missing = df["district"].isna().sum()
        if missing and verbose:
            # 會發生在「站點被撤掉、stations.parquet 更新了但舊快照還留著它」的時候
            print("注意：有 {} 列找不到對應的站點基本資料".format(int(missing)))

    df = df.sort_values(["station_id", "src_time"]).reset_index(drop=True)

    if verbose:
        print("完成：{:,} 列、{} 站、{} 天".format(
            len(df), df["station_id"].nunique(), df["date"].nunique()))
    return df


# ---------------------------------------------------------------------------
# 資料盤點報告
# ---------------------------------------------------------------------------

def summary(data_dir=None, show_days=14):
    """
    印出「手上有什麼資料」的盤點報告。

    每次開始分析前先跑這個 —— 在算任何統計之前先知道資料的形狀和缺口，
    比算完才發現「原來有三天是空的」省時間太多。
    """
    data_dir = data_dir or default_data_dir()
    days = available_days(data_dir)

    print("資料位置：{}".format(data_dir))
    print("有資料的日子：{} 天".format(len(days)))
    if not days:
        return
    print("範圍：{} ~ {}".format(days[0]["date"], days[-1]["date"]))
    print()

    print("{:<12} {:<12} {:>8} {:>10}".format("日期", "形式", "時間點", "覆蓋率"))
    print("-" * 46)

    shown = days[-show_days:] if show_days and len(days) > show_days else days
    if len(shown) < len(days):
        print("（只列出最後 {} 天，共 {} 天）".format(len(shown), len(days)))

    total_points = 0
    for day in shown:
        # 時間點數 = 不同 src_time 的數量。
        # 小檔的話一個檔就是一個時間點，直接數檔案比讀檔快得多；
        # 日檔就得真的讀出 src_time 欄位（只讀這一欄，不讀整張表）。
        if day["form"] == "小檔":
            n_points = day["n_files"]
        else:
            seen = set()
            for p in day["paths"]:
                seen.update(pq.read_table(p, columns=["src_time"])
                            .column("src_time").to_pylist())
            n_points = len(seen)
        total_points += n_points
        cov = 100.0 * n_points / EXPECTED_PER_DAY
        flag = "" if cov >= 90 else ("  ← 偏低" if cov >= 50 else "  ← 嚴重不足")
        print("{:<12} {:<12} {:>8} {:>9.1f}%{}".format(
            day["date"], day["form"], n_points, cov, flag))

    print("-" * 46)
    print("列出的天數共 {} 個時間點（一天滿載是 {} 個）".format(total_points, EXPECTED_PER_DAY))
    print()

    try:
        st = load_stations(data_dir)
        print("站點基本資料：{} 站".format(len(st)))
        top = st["district"].value_counts().head(5)
        print("站數最多的行政區：" + "、".join(
            "{} {} 站".format(d, n) for d, n in top.items()))
        # 建模範圍已定案是大安區（見 docs/DECISIONS.md），順手把它的站數印出來
        n_daan = int((st["district"] == "大安區").sum())
        print("大安區（第一個模型的範圍）：{} 站".format(n_daan))
    except DataNotFound as e:
        print("（讀不到站點基本資料：{}）".format(e))


if __name__ == "__main__":
    summary()
