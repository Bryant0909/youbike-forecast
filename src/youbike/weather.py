"""
天氣資料（中央氣象署自動氣象站 O-A0003-001）的讀取與換算。

┌─ 最重要的一件事：precip_mm 是「當天累積雨量」，不是「現在的雨量」 ─┐
│ 2026-10-07 用 9/30~10/6 一整週、363 站的資料確認：                  │
│   - 一週內數值「下降」442 次，100% 發生在 00:10，94% 直接歸零        │
│     （沒歸零的是 00:00~00:10 之間剛好在下雨）                        │
│   - 00:00 那筆一定 >= 前一筆 23:50 —— 00:00 還算「前一天」的總量，   │
│     00:10 才是新的一天的第一筆                                      │
│ 所以它是「從當天 00:00 起算的累積毫米數」。                          │
│                                                                    │
│ 直接拿 precip_mm 當特徵會出大事：晚上 8 點一個早上下過大雨、現在     │
│ 早就放晴的站，數值會跟「正在下大雨」的站一樣高。模型要的是            │
│ 「最近這段時間下了多少」，請用 rain_increment() 換算出 rain_mm。     │
└────────────────────────────────────────────────────────────────────┘

用法：

    from youbike.weather import load_weather, rain_increment

    w = load_weather(start="2026-10-01", end="2026-10-06",
                     station_ids=["CAAH60", "A0A010"])   # 大安森林、臺灣大學
    w = rain_increment(w)        # 多出 rain_mm、rain_interval_min 兩欄
"""

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from youbike.data import TAIPEI_TZ, DataNotFound, available_days, default_data_dir

# 大安區裡的兩個自動氣象站。建模範圍（大安區 + 臺大公館校區）沒有其他測站，
# 「臺灣大學」這站本身就在校園裡，所以兩站就夠了
DAAN_STATIONS = ["CAAH60", "A0A010"]   # 大安森林、臺灣大學


def load_weather(start=None, end=None, station_ids=None, data_dir=None):
    """
    讀出指定範圍的天氣觀測，回傳 pandas DataFrame（依 測站、時間 排序）。

    start, end   日期字串 'YYYY-MM-DD'，含頭含尾（照「檔案放在哪一天」篩，
                 也就是台北時間的日期 —— 00:00 那筆放在新的一天的檔案裡）
    station_ids  只要哪些測站，例如 DAAN_STATIONS。None = 全部 363 站

    多加一欄 time_taipei（台北時間）方便看。
    """
    data_dir = data_dir or default_data_dir()
    days = available_days(data_dir, subdir="weather")
    if start:
        days = [d for d in days if d["date"] >= start]
    if end:
        days = [d for d in days if d["date"] <= end]
    if not days:
        raise DataNotFound("指定範圍（start={}, end={}）內沒有天氣資料".format(start, end))

    id_set = pa.array(station_ids, type=pa.string()) if station_ids else None
    tables = []
    for day in days:
        for path in day["paths"]:
            t = pq.read_table(path)
            if id_set is not None:
                t = t.filter(pc.is_in(t.column("station_id"), value_set=id_set))
            if t.num_rows:
                tables.append(t)
    if not tables:
        raise DataNotFound("篩選之後一列都不剩，請檢查 station_ids")

    df = pa.concat_tables(tables).to_pandas()
    # 壓縮做到一半中斷時日檔和小檔會同時存在，同一筆觀測就會出現兩次
    df = df.drop_duplicates(subset=["station_id", "obs_time"], keep="first")
    df["time_taipei"] = df["obs_time"].dt.tz_convert(TAIPEI_TZ)
    return df.sort_values(["station_id", "obs_time"]).reset_index(drop=True)


def rain_increment(df):
    """
    把「當天累積雨量」precip_mm 換算成「跟上一筆觀測之間下了多少」。

    新增兩欄：
        rain_mm             這段期間的雨量（毫米）。正常情況下就是「這 10 分鐘下了多少」
        rain_interval_min   這段期間有幾分鐘（正常是 10；中間缺資料就會比較長，
                            想換算成「每 10 分鐘的雨量」可以用 rain_mm / 間隔 * 10）

    換算規則：
      1. 先決定每筆觀測屬於「哪一天的累積」—— 00:00 那筆屬於前一天
         （它是前一天的總量），所以用「台北時間減 1 分鐘」的日期當累積日。
      2. 同一個累積日裡：rain_mm = 這筆 − 上一筆
      3. 累積日的第一筆（正常是 00:10）：前面沒有可以減的，
         rain_mm = 它自己（從 00:00 到現在累積的量）
      4. 任何一邊是空值（氣象署 -99 轉成的 null）就是空值 —— 不猜
      5. 同一天裡居然變小（儀器修正之類）也當空值，不硬塞成 0：
         0 代表「確定沒下雨」，跟「不知道」是兩回事
    """
    import pandas as pd

    out = df.sort_values(["station_id", "obs_time"]).copy()
    local = out["obs_time"].dt.tz_convert(TAIPEI_TZ)
    acc_day = (local - pd.Timedelta(minutes=1)).dt.date   # 規則 1

    group = [out["station_id"], acc_day]
    prev_val = out.groupby(group)["precip_mm"].shift()
    prev_time = out.groupby(group)["obs_time"].shift()
    first_of_day = prev_time.isna()

    diff = out["precip_mm"] - prev_val                         # 規則 2
    rain = diff.where(~first_of_day, out["precip_mm"])         # 規則 3
    rain = rain.where(rain >= 0)                               # 規則 4、5（NaN >= 0 也是 False）

    # 累積日的第一筆，期間是從 00:00 起算
    day_start = pd.to_datetime(acc_day.astype(str)).dt.tz_localize(TAIPEI_TZ)
    start = prev_time.dt.tz_convert(TAIPEI_TZ).where(~first_of_day, day_start)
    out["rain_mm"] = rain.astype("float32")
    out["rain_interval_min"] = ((local - start).dt.total_seconds() / 60).astype("float32")
    return out


# ---------------------------------------------------------------------------
# phase 3-6：天氣特徵
# ---------------------------------------------------------------------------

WEATHER_FEATURES = ["rain_30m", "rain_60m", "temp_c"]

# 最新一筆天氣超過這麼久還沒更新，就當作「不知道」（NaN），不要拿很舊的天氣硬接
WEATHER_MAX_AGE_MIN = 40


def weather_features(sample_times, weather):
    """
    把天氣接到樣本上：t 那一刻「已經拿到」的最新天氣。

    【用拿到的時間，不是觀測時間】
    氣象署 16:00 的觀測，我們實測要 10~25 分鐘後（平均 12.6 分鐘）才拿得到。
    如果用觀測時間對齊，16:05 的樣本就會用到 16:00 那筆 —— 但真的在 16:05
    那一刻，那筆根本還沒公布。所以這裡用 fetched_at（我們實際收到的時間）對齊：
    只用 fetched_at <= t 的觀測。

        rain_30m    最近 30 分鐘（3 筆觀測）下了幾毫米
        rain_60m    最近 60 分鐘（6 筆觀測）下了幾毫米
        temp_c      氣溫

    weather   load_weather(station_ids=DAAN_STATIONS) 的結果（多個測站會取平均）
    回傳 DataFrame，index 跟 sample_times 一樣。
    """
    import warnings

    import numpy as np
    import pandas as pd

    cols = ["rain_30m", "rain_60m", "temp_c"]
    if weather is None or len(weather) == 0:
        # 天氣收集失敗（氣象署掛掉、金鑰過期…）時不要讓預測整個停掉：
        # 天氣特徵全部當成「不知道」（NaN），LightGBM 本來就會處理缺值
        return pd.DataFrame(np.nan, columns=cols, index=sample_times.index, dtype="float32")

    w = rain_increment(weather)
    w = w.sort_values(["station_id", "obs_time"])
    # 以觀測時間為視窗加總雨量：(obs − 30 分, obs]。雨量是空值的那幾筆不算進去，
    # 但視窗裡一筆有效值都沒有時就是空值（不知道），不是 0
    parts = []
    for sid, g in w.groupby("station_id"):
        r = g.set_index("obs_time")["rain_mm"]
        parts.append(pd.DataFrame({
            "station_id": sid,
            "available_at": g["fetched_at"].reset_index(drop=True),
            "rain_30m": r.rolling("30min", min_periods=1).sum().to_numpy(),
            "rain_60m": r.rolling("60min", min_periods=1).sum().to_numpy(),
            "temp_c": g["temp_c"].to_numpy(),
        }))
    per_station = pd.concat(parts, ignore_index=True).sort_values("available_at")

    left = pd.DataFrame({"t": sample_times.astype(per_station["available_at"].dtype).reset_index(drop=True),
                         "_pos": np.arange(len(sample_times))}).sort_values("t")
    stacked = []
    for sid, g in per_station.groupby("station_id"):
        m = pd.merge_asof(left, g[["available_at"] + cols], left_on="t", right_on="available_at",
                          direction="backward", tolerance=pd.Timedelta(minutes=WEATHER_MAX_AGE_MIN))
        stacked.append(m.sort_values("_pos")[cols].to_numpy(dtype="float64"))
    # 幾個測站取平均（某一站缺資料時就用另一站）
    # （兩站都沒資料時 nanmean 會警告「空的平均」，結果是 NaN，正是我們要的）
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=RuntimeWarning)
        avg = np.nanmean(np.stack(stacked), axis=0)
    return pd.DataFrame(avg.astype("float32"), columns=cols, index=sample_times.index)
