"""
phase 0-7：天氣資料收集程式（中央氣象署 自動氣象站）。

┌─ 為什麼現在就要收天氣，明明 phase 3 才會用到？ ────────────────────┐
│ 因為天氣的歷史觀測，事後要「對齊到每 5 分鐘的時間點」非常麻煩，     │
│ 而且免費的歷史資料通常只有粗粒度（每小時、甚至每天）。             │
│ 現在順手存下來，成本接近零；等三個月後才想收，那三個月就補不回來了。│
│ 這跟 YouBike 資料「反悔成本無限大」是同一個道理。                   │
└────────────────────────────────────────────────────────────────────┘

【為什麼選 O-A0003-001（自動氣象站）而不是 O-A0001-001（現在天氣觀測報告）】
  比較過三支：
    O-A0001-001  876 站、臺北市 19 站，但觀測時間【每小時】整點
    O-A0003-001  363 站、臺北市  9 站，觀測時間【每 10 分鐘】，欄位更多
    O-A0002-001  1339 站（雨量專用），每 10 分鐘，但只有雨量
  選 O-A0003-001 的理由：
    1. 10 分鐘 vs 每小時 —— 突發陣雨正是 YouBike 需求暴衝的主因，
       每小時一筆會把一場 20 分鐘的雷陣雨整個抹平。
    2. 它有【大安森林 CAAH60】和【臺灣大學 A0A010】兩站，
       兩站都在大安區 —— 正好是第一個模型的範圍。
    3. 多了日照、紫外線、10 分鐘最大風速等欄位。

設計上沿用 collect.py 的三個原則（細節見該檔註解）：
  1. 靜態 / 動態分開存（測站名稱、經緯度只存一份）
  2. 用來源的觀測時間當檔名 —— 天然去重
     （實測全部 363 站共用同一個 ObsTime，跟 YouBike 的 mday 一樣）
  3. 只新增、不修改

【-99 的陷阱】
  氣象署用 "-99" 表示「這個值沒有／儀器故障」，而且連文字欄位也是
  （實測有 2 站的天氣現象是 "-99"）。如果直接存進去，之後模型會看到
  -99 度的氣溫、-99% 的濕度 —— 這種資料不會報錯，只會安靜地毀掉模型。
  所以一律轉成 null（空值），讓它在分析時明確地「不存在」而不是「是個怪數字」。

用法：
    python scripts/collect_weather.py              # 抓一次並存檔
    python scripts/collect_weather.py --dry-run    # 只抓不存，用來測試

金鑰來源（依序嘗試）：
    1. 環境變數 CWA_API_KEY（GitHub Actions 走這條，值放在 repo secret）
    2. 專案根目錄的 .env 檔（本機開發走這條，.env 不會被 commit）
"""

import argparse
import io
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

API_URL = "https://opendata.cwa.gov.tw/api/v1/rest/datastore/O-A0003-001"

TAIPEI_TZ = timezone(timedelta(hours=8))

# 路徑從程式檔位置推算，不依賴執行時的工作目錄（理由見 collect.py）
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_OUT_DIR = os.path.join(REPO_ROOT, "data", "raw")

RETRY_WAITS = [2, 5, 10]

# 合理性檢查：正常應該有 300 站以上。只拿到幾十站代表來源出問題，
# 這種半殘的資料存下去比沒存更糟（之後很難發現）。
MIN_EXPECTED_STATIONS = 200

# 氣象署用 -99 / -990 之類的值表示「沒有這個資料」。
# 台灣的氣溫不可能低於 -90 度，所以用這個門檻判斷很安全。
MISSING_THRESHOLD = -90.0

SNAPSHOT_SCHEMA = pa.schema([
    ("station_id", pa.string()),
    # 全部量測值都用 float32 + 可為空。
    # 為什麼不用整數？因為每個欄位都可能是 null（儀器故障），
    # 而 null 在整數欄位裡處理起來比較彆扭；float32 精度對氣象值也綽綽有餘。
    ("temp_c", pa.float32()),            # 氣溫（攝氏）
    ("humidity_pct", pa.float32()),      # 相對濕度（%）
    ("pressure_hpa", pa.float32()),      # 氣壓（百帕）
    ("wind_speed_ms", pa.float32()),     # 風速（公尺/秒）
    ("wind_dir_deg", pa.float32()),      # 風向（度，0=北）
    ("gust_ms", pa.float32()),           # 陣風最大風速（公尺/秒）
    ("precip_mm", pa.float32()),         # 雨量（毫米）<- 對 YouBike 影響最大的一項
                                         # ⚠️ 是「當天 00:00 起的累積量」（2026-10-07 實測確認），
                                         #    不能直接當特徵，要用 youbike.weather.rain_increment() 換算
    ("uv_index", pa.float32()),          # 紫外線指數
    ("sunshine", pa.float32()),          # 日照（氣象署未標單位，見下方註解）
    ("weather", pa.string()),            # 天氣現象文字（晴／多雲／多雲有雨…）
    ("obs_time", pa.timestamp("s", tz="UTC")),    # 觀測時間（來源給的）
    ("fetched_at", pa.timestamp("s", tz="UTC")),  # 我們抓取的時間
])

STATIONS_SCHEMA = pa.schema([
    ("station_id", pa.string()),
    ("name", pa.string()),
    ("county", pa.string()),
    ("town", pa.string()),
    ("lat", pa.float64()),
    ("lon", pa.float64()),
    ("altitude_m", pa.float32()),
    ("first_seen", pa.timestamp("s", tz="UTC")),
    ("last_seen", pa.timestamp("s", tz="UTC")),
])


def log(msg):
    print("[{:%H:%M:%S}] {}".format(datetime.now(TAIPEI_TZ), msg), flush=True)


def rel(path):
    try:
        return os.path.relpath(path, REPO_ROOT).replace("\\", "/")
    except ValueError:
        return path


# ---------------------------------------------------------------------------
# 金鑰
# ---------------------------------------------------------------------------

def load_api_key():
    """
    取得氣象署 API key。

    兩個來源：Actions 上用環境變數（值來自 repo secret），
    本機用 .env（不會被 commit）。找不到就給出明確的修正指示 ——
    「KeyError: CWA_API_KEY」這種訊息對半年後的自己毫無幫助。
    """
    key = os.environ.get("CWA_API_KEY", "").strip()
    if key:
        return key

    env_path = os.path.join(REPO_ROOT, ".env")
    if os.path.isfile(env_path):
        for line in io.open(env_path, encoding="utf-8"):
            line = line.strip()
            if line.startswith("CWA_API_KEY="):
                key = line.split("=", 1)[1].strip()
                if key:
                    return key

    raise RuntimeError(
        "找不到 CWA_API_KEY。\n"
        "  本機：複製 .env.example 成 .env，把金鑰填進 CWA_API_KEY=\n"
        "  Actions：到 repo 的 Settings -> Secrets and variables -> Actions 設定 CWA_API_KEY\n"
        "  申請金鑰：https://opendata.cwa.gov.tw/ -> 註冊 -> 會員資訊 -> API 授權碼"
    )


# ---------------------------------------------------------------------------
# 解析工具
# ---------------------------------------------------------------------------

def num(value):
    """
    把氣象署回傳的字串轉成數字，把 -99 之類的缺失標記轉成 None。

    這個函式是整支程式最重要的一段 —— 見檔案開頭「-99 的陷阱」。
    """
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f <= MISSING_THRESHOLD:
        return None
    return f


def text(value):
    """文字欄位的缺失處理（實測天氣現象也會是 "-99"）。"""
    if value is None:
        return None
    s = str(value).strip()
    if not s or s.startswith("-99"):
        return None
    return s


def parse_obs_time(s):
    """
    把 '2026-09-30T17:40:00+08:00' 轉成 UTC 的 datetime。

    跟 YouBike 一樣一律轉成 UTC 再存 —— UTC 沒有時區換算的模糊地帶，
    之後做時間計算（例如「30 分鐘後」）不會出錯。
    """
    return datetime.fromisoformat(s).astimezone(timezone.utc).replace(microsecond=0)


def wgs84(geo):
    """
    取得 WGS84 座標。

    氣象署同時給 TWD67 和 WGS84 兩套座標系，兩者差了數百公尺。
    YouBike 的經緯度是 WGS84，所以這裡也一定要取 WGS84，
    不然之後算「站點離哪個氣象站最近」會整個偏掉。
    """
    for c in geo.get("Coordinates", []):
        if c.get("CoordinateName") == "WGS84":
            return float(c["StationLatitude"]), float(c["StationLongitude"])
    return None, None


# ---------------------------------------------------------------------------
# 抓取
# ---------------------------------------------------------------------------

def fetch_with_retry(key):
    last_err = None
    for attempt in range(len(RETRY_WAITS) + 1):
        try:
            resp = requests.get(API_URL,
                                params={"Authorization": key, "format": "JSON"},
                                timeout=30)
            resp.raise_for_status()
            data = resp.json()
            if str(data.get("success")).lower() != "true":
                raise ValueError("API 回應 success != true（金鑰可能無效或過期）")
            stations = data["records"]["Station"]
            if not isinstance(stations, list):
                raise ValueError("預期 records.Station 是 list")
            return stations
        except Exception as e:
            last_err = e
            if attempt < len(RETRY_WAITS):
                wait = RETRY_WAITS[attempt]
                log("第 {} 次嘗試失敗（{}），{} 秒後重試…".format(
                    attempt + 1, type(e).__name__, wait))
                time.sleep(wait)
    raise RuntimeError("重試 {} 次都失敗：{}".format(len(RETRY_WAITS) + 1, last_err))


def atomic_write_parquet(table, path):
    """先寫暫存檔再改名，避免中途被中斷時留下半個壞檔（理由見 collect.py）。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    pq.write_table(table, tmp, compression="zstd")
    os.replace(tmp, path)


def build_snapshot(records, obs_time, fetched_at):
    """把 API 回傳的巢狀結構攤平成一張表。"""
    cols = {name: [] for name in SNAPSHOT_SCHEMA.names}
    for r in records:
        we = r.get("WeatherElement", {})
        cols["station_id"].append(str(r.get("StationId")))
        cols["temp_c"].append(num(we.get("AirTemperature")))
        cols["humidity_pct"].append(num(we.get("RelativeHumidity")))
        cols["pressure_hpa"].append(num(we.get("AirPressure")))
        cols["wind_speed_ms"].append(num(we.get("WindSpeed")))
        cols["wind_dir_deg"].append(num(we.get("WindDirection")))
        cols["gust_ms"].append(num(we.get("GustInfo", {}).get("PeakGustSpeed")))
        cols["precip_mm"].append(num(we.get("Now", {}).get("Precipitation")))
        cols["uv_index"].append(num(we.get("UVIndex")))
        cols["sunshine"].append(num(we.get("SunshineDuration")))
        cols["weather"].append(text(we.get("Weather")))

    n = len(records)
    cols["obs_time"] = [obs_time] * n
    cols["fetched_at"] = [fetched_at] * n
    return pa.Table.from_pydict(cols, schema=SNAPSHOT_SCHEMA)


def update_stations(records, now, path, dry_run):
    """
    更新測站基本資料。跟 collect.py 一樣：【只有真的變了才寫檔】，
    否則每次執行都會產生一個無意義的變更紀錄。
    """
    existing = {}
    if os.path.exists(path):
        for r in pq.read_table(path).to_pylist():
            existing[r["station_id"]] = r

    changed = 0
    for r in records:
        sid = str(r.get("StationId"))
        geo = r.get("GeoInfo", {})
        lat, lon = wgs84(geo)
        row = {
            "station_id": sid,
            "name": text(r.get("StationName")),
            "county": text(geo.get("CountyName")),
            "town": text(geo.get("TownName")),
            "lat": lat,
            "lon": lon,
            "altitude_m": num(geo.get("StationAltitude")),
        }
        prev = existing.get(sid)
        if prev is None:
            row["first_seen"] = now
            row["last_seen"] = now
            existing[sid] = row
            changed += 1
        else:
            diff = any(prev.get(k) != row[k] for k in
                       ("name", "county", "town", "lat", "lon", "altitude_m"))
            prev.update(row)
            prev["last_seen"] = now
            if diff:
                changed += 1

    if changed == 0 and os.path.exists(path):
        return "沒變動，不寫檔"
    if dry_run:
        return "[dry-run] 會更新 {} 站（共 {} 站）".format(changed, len(existing))

    rows = sorted(existing.values(), key=lambda x: x["station_id"])
    atomic_write_parquet(pa.Table.from_pylist(rows, schema=STATIONS_SCHEMA), path)
    return "更新了 {} 站（共 {} 站）".format(changed, len(existing))


def main():
    ap = argparse.ArgumentParser(description="抓一次中央氣象署自動氣象站觀測並存成 Parquet")
    ap.add_argument("--out-dir", default=DEFAULT_OUT_DIR,
                    help="資料存放資料夾（預設是專案裡的 data/raw，不受執行位置影響）")
    ap.add_argument("--dry-run", action="store_true", help="只抓資料不存檔，用來測試")
    args = ap.parse_args()

    key = load_api_key()
    fetched_at = datetime.now(timezone.utc).replace(microsecond=0)
    log("開始抓取：自動氣象站（O-A0003-001）")

    records = fetch_with_retry(key)
    log("拿到 {} 站".format(len(records)))

    if len(records) < MIN_EXPECTED_STATIONS:
        raise ValueError("只拿到 {} 站，少於預期的 {} 站，來源可能出問題，這次不存".format(
            len(records), MIN_EXPECTED_STATIONS))

    # 實測全部測站共用同一個 ObsTime；取最大值是為了保險。
    obs_time = max(parse_obs_time(r["ObsTime"]["DateTime"])
                   for r in records if r.get("ObsTime", {}).get("DateTime"))
    obs_local = obs_time.astimezone(TAIPEI_TZ)
    lag = (fetched_at - obs_time).total_seconds()
    log("觀測時間：{:%Y-%m-%d %H:%M:%S}（台北），比現在舊 {:.0f} 秒".format(obs_local, lag))

    # 檔名用觀測時間 —— 同一份觀測不管被抓幾次都對應同一個檔名，天然去重。
    snap_path = os.path.join(
        args.out_dir, "weather",
        "{:%Y-%m-%d}".format(obs_local),
        "{:%Y%m%dT%H%M%S}.parquet".format(obs_local),
    )

    if os.path.exists(snap_path):
        log("跳過：這份觀測已經存過了（{}）".format(os.path.basename(snap_path)))
        log("（氣象署每 10 分鐘才更新一次，所以大部分執行都會跳過，這是正常的）")
        return 0

    table = build_snapshot(records, obs_time, fetched_at)

    if args.dry_run:
        log("[dry-run] 不寫檔。表格大小：{} 列 x {} 欄".format(
            table.num_rows, table.num_columns))
        log("[dry-run] 本來會寫到：" + rel(snap_path))
    else:
        atomic_write_parquet(table, snap_path)
        log("已存檔：{}（{:.1f} KB）".format(
            rel(snap_path), os.path.getsize(snap_path) / 1024))

    stations_path = os.path.join(args.out_dir, "weather_stations.parquet")
    log("測站基本資料：" + update_stations(records, fetched_at, stations_path, args.dry_run))

    # 印一個摘要，這樣在 Actions 紀錄裡一眼就能看出資料正不正常。
    # 特別挑大安區的兩站，因為那是第一個模型的範圍。
    daan = [r for r in records if r.get("GeoInfo", {}).get("TownName") == "大安區"]
    for r in daan:
        we = r.get("WeatherElement", {})
        log("  大安區 {}：{}，氣溫 {}°C，濕度 {}%，雨量 {} mm".format(
            r.get("StationName"), text(we.get("Weather")) or "—",
            num(we.get("AirTemperature")), num(we.get("RelativeHumidity")),
            num(we.get("Now", {}).get("Precipitation"))))

    raining = sum(1 for r in records
                  if (num(r.get("WeatherElement", {}).get("Now", {}).get("Precipitation")) or 0) > 0)
    log("全台 {} 站中有 {} 站正在下雨".format(len(records), raining))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        log("執行失敗：{}: {}".format(type(e).__name__, e))
        sys.exit(1)
