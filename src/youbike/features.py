"""
phase 3：特徵（模型的「輸入」）。

┌─ 這個檔案最重要的一條規則：不能偷看未來 ───────────────────────────┐
│ 一筆樣本是「某站、時間 t」，要預測的是 t + 30 分鐘。                 │
│ 所以每個特徵都只能用「t 那一刻（含）以前」就知道的事。               │
│                                                                    │
│ 違反這條規則的特徵，訓練和測試時分數都會很漂亮，                     │
│ 真的上線（只有過去的資料）就完全不準 —— 而且你不會發現。             │
│ 所以每個特徵旁邊都寫「它往回看到哪裡」，而且                         │
│ tests/test_features.py 會實際檢查：把 t 之後的資料全部刪掉重算，     │
│ t 以前的特徵值必須一模一樣。                                         │
└────────────────────────────────────────────────────────────────────┘

特徵分成幾組（FEATURE_GROUPS），每組對應 phase 3 的一個步驟，
之後看「哪一組有用」（3-9）時可以整組拿掉比較：

    station   3-2  這一站自己：現在幾台、最近一小時怎麼變、昨天同一時間幾台
    calendar  3-3  時間：幾點、星期幾、是不是放假
    profile   3-4  站的類型：哪一區、在哪裡、平常有多容易空
    citywide  3-5  全台北現在總共有多少車可借
    weather   3-6  最近有沒有下雨、氣溫

用法：

    from youbike.features import build_features, feature_columns

    X = build_features(samples, history)   # 站點、全市、天氣資料沒給的話會自己讀
    cols = feature_columns()               # 全部特徵的欄位名稱
"""

import numpy as np
import pandas as pd

from youbike.calendar_tw import CALENDAR_FEATURES, calendar_features
from youbike.citywide import CITYWIDE_FEATURES, citywide_features
from youbike.dataset import HORIZON_MIN, TOLERANCE_MIN
from youbike.weather import WEATHER_FEATURES, weather_features

# 「快沒車」的定義：跟預測目標一樣是 <= 1（docs/DECISIONS.md 2026-11-03）。
# 用在「最近一次快沒車是多久以前」這類特徵。
LOW_BIKES = 1

# 往回看幾分鐘的車數。5 分鐘是一次取樣、60 分鐘是一小時。
LAG_MINUTES = (5, 15, 30, 60)

# 「距離上次快沒車多久」最多記到一天；更久的都當成一天（再久也沒差別）。
MAX_SINCE_LOW_MIN = 24 * 60

FEATURE_GROUPS = {
    "station": (
        ["bikes_available", "docks_available", "total_docks", "fill_ratio"]
        + ["bikes_lag{}".format(m) for m in LAG_MINUTES]
        + ["diff{}".format(m) for m in LAG_MINUTES]
        + ["min_60m", "max_60m", "min_since_low",
           "bikes_1d_at_target", "bikes_7d_at_target"]
    ),
    "calendar": CALENDAR_FEATURES,
    "profile": ["is_campus", "lat", "lon", "low_rate_all", "low_rate_slot", "slot_days"],
    "citywide": CITYWIDE_FEATURES,
    "weather": WEATHER_FEATURES,
}


def feature_columns(groups=None):
    """要用的特徵欄位（依 FEATURE_GROUPS 的順序）。groups=None 就是全部。"""
    groups = groups or list(FEATURE_GROUPS)
    return [c for g in groups for c in FEATURE_GROUPS[g]]


# ---------------------------------------------------------------------------
# 3-2：這一站自己的特徵
# ---------------------------------------------------------------------------

def _value_at(history, station_ids, want_times, col="bikes_available",
              tolerance_min=TOLERANCE_MIN):
    """
    查「某站在某個時間點」的值：在 want_times 前後 tolerance 分鐘內找最近的一筆。

    跟 dataset.make_labels 找答案用的是同一招（merge_asof），只是方向相反：
    這裡查的是「過去」的時間點。只要 want_time <= t - 5 分鐘、
    容許範圍 ±2.5 分鐘，查到的那一筆一定在 t 之前，不會偷看未來。

    回傳一個跟 want_times 同順序的 numpy 陣列，找不到就是 NaN。
    """
    left = pd.DataFrame({
        "station_id": np.asarray(station_ids),
        "_want": pd.to_datetime(want_times).astype(history["src_time"].dtype),
        "_pos": np.arange(len(want_times)),
    })
    right = history[["station_id", "src_time", col]].rename(columns={col: "_val"})
    merged = pd.merge_asof(
        left.sort_values("_want"), right.sort_values("src_time"),
        left_on="_want", right_on="src_time", by="station_id",
        direction="nearest", tolerance=pd.Timedelta(minutes=tolerance_min),
    )
    out = np.full(len(left), np.nan)
    out[merged["_pos"].to_numpy()] = merged["_val"].to_numpy(dtype=float)
    return out


def station_features(history):
    """
    每一列（站, t）算出這一站自己的特徵。history 是 load_snapshots() 的結果，
    要包含「樣本之前」的資料（例如一小時前、昨天），所以要給完整的歷史，
    不能只給有標籤的那幾列。

    回傳以 (station_id, src_time) 為鍵的 DataFrame。

    每個特徵往回看到哪裡：
        bikes_available, docks_available, total_docks   t 當下
        fill_ratio          t 當下：可借車輛 / 總車柱數（同樣 3 台，10 柱的站跟 60 柱的站意義不同）
        bikes_lagN          t − N 分鐘的車數（N = 5, 15, 30, 60）
        diffN               現在 − N 分鐘前：正的 = 最近有人還車、負的 = 一直被借走
        min_60m, max_60m    最近 60 分鐘內（含 t）最少／最多幾台
        min_since_low       距離上一次「<= 1 台」幾分鐘（現在就是 <= 1 台 = 0）。
                            EDA 發現「剛空過、剛有人還一台」的站最容易再空（phase 1-3）
        bikes_1d_at_target  昨天「答案那個時刻」（t + 30 分 − 24 小時）幾台
        bikes_7d_at_target  上週同一天的答案時刻（t + 30 分 − 7 天）幾台
                            這兩個在時間上都遠早於 t，不算偷看
    """
    h = history.sort_values(["station_id", "src_time"]).reset_index(drop=True)
    out = h[["station_id", "src_time", "bikes_available", "docks_available", "total_docks"]].copy()

    bikes = h["bikes_available"].astype("float32")
    docks = h["total_docks"].astype("float32")
    out["fill_ratio"] = (bikes / docks.where(docks > 0)).astype("float32")

    for m in LAG_MINUTES:
        lag = _value_at(h, h["station_id"], h["src_time"] - pd.Timedelta(minutes=m))
        out["bikes_lag{}".format(m)] = lag.astype("float32")
        out["diff{}".format(m)] = (bikes.to_numpy() - lag).astype("float32")

    # 最近 60 分鐘的最少／最多：用「時間」當視窗（不是「前 12 筆」），
    # 因為資料有空洞時，前 12 筆可能橫跨好幾個小時。
    # pandas 的時間視窗預設是 (t − 60 分, t]：包含現在、不包含未來。
    roll = (h.set_index("src_time").groupby("station_id", sort=False)["bikes_available"]
            .rolling("60min"))
    out["min_60m"] = roll.min().to_numpy().astype("float32")
    out["max_60m"] = roll.max().to_numpy().astype("float32")

    # 距離上次 <= 1 台多久：把「<= 1 台」那幾列的時間往下填（同一站內），
    # 就是每一列「最近一次 <= 1 台」的時間。往下填只會用到上面（更早）的列。
    low_time = h["src_time"].where(h["bikes_available"] <= LOW_BIKES)
    last_low = low_time.groupby(h["station_id"]).ffill()
    since = (h["src_time"] - last_low).dt.total_seconds() / 60
    # 從資料開頭到現在都沒空過 -> NaN，也當成「很久了」
    out["min_since_low"] = since.fillna(MAX_SINCE_LOW_MIN).clip(upper=MAX_SINCE_LOW_MIN).astype("float32")

    target = h["src_time"] + pd.Timedelta(minutes=HORIZON_MIN)
    for name, days in (("bikes_1d_at_target", 1), ("bikes_7d_at_target", 7)):
        out[name] = _value_at(h, h["station_id"], target - pd.Timedelta(days=days)).astype("float32")

    return out


# ---------------------------------------------------------------------------
# 3-4：站的類型
# ---------------------------------------------------------------------------

def _past_days_rate(daily, lookup, by):
    """
    「t 那天以前的日子」裡，某個格子（例如某站、休息日、18 點）<= 1 台的比例。

    daily   每天每格的 (low = <= 1 台的筆數, n = 總筆數)，欄位 by + ["day", "low", "n"]
    lookup  要查的列，欄位 by + ["day"]（day = t 的日期）

    做法：同一格依日期累加，再用 merge_asof 找「日期嚴格早於 t 那天」的最後一筆
    （allow_exact_matches=False）。所以 t 當天的資料（包含 t 之後的時刻）完全不會用到。
    回傳 (rate, n_days)，跟 lookup 同順序。
    """
    d = daily.sort_values(by + ["day"]).copy()
    g = d.groupby(by, sort=False)
    d["cum_low"] = g["low"].cumsum()
    d["cum_n"] = g["n"].cumsum()
    d["cum_days"] = g.cumcount() + 1

    left = lookup[by + ["day"]].copy()
    left["_pos"] = np.arange(len(left))
    merged = pd.merge_asof(
        left.sort_values("day"), d[by + ["day", "cum_low", "cum_n", "cum_days"]].sort_values("day"),
        on="day", by=by, direction="backward", allow_exact_matches=False,
    ).sort_values("_pos")
    rate = (merged["cum_low"] / merged["cum_n"]).to_numpy(dtype="float32")
    days = merged["cum_days"].fillna(0).to_numpy(dtype="int16")
    return rate, days


def profile_features(history, samples, stations):
    """
    這個站「是哪一種站」。EDA 發現兩區節奏相反：大安區上班日早上最難借、
    校區晚上最難借（phase 1-3），模型必須知道它在看的是哪一種站。

        is_campus           是不是臺大公館校區的站
        lat, lon            位置（樹模型可以自己切出「公館一帶」「信義安和一帶」）
        low_rate_all        過去的日子裡，這站 <= 1 台的時間比例（不分時段）
        low_rate_slot       過去的日子裡，這站在「同一種日子（上班／休息）、
                            答案時刻同一個小時」<= 1 台的比例 ——
                            就是 baseline ②「歷史同時段平均」的概念，變成一個特徵
        slot_days           low_rate_slot 是用幾天算的（剛開始收資料時只有一兩天，不太可信）

    「過去的日子」= t 那天以前（不含當天）。每一筆樣本都只用它自己的過去，
    所以不用分「訓練期才能算」，也不會偷看未來。
    代價是最前面幾天沒有歷史（NaN），LightGBM 會把 NaN 當成「不知道」處理。
    """
    from youbike.calendar_tw import is_offday

    h = history[["station_id", "src_time", "bikes_available"]].copy()
    local = h["src_time"].dt.tz_convert("Asia/Taipei")
    h["day"] = local.dt.normalize().dt.tz_localize(None)
    h["hour"] = local.dt.hour.astype("int8")
    off = {d: is_offday(d) for d in h["day"].unique()}
    h["offday"] = h["day"].map(off)
    h["low"] = (h["bikes_available"] <= LOW_BIKES).astype("int32")

    daily_slot = (h.groupby(["station_id", "offday", "hour", "day"], observed=True)["low"]
                  .agg(low="sum", n="count").reset_index())
    daily_all = (h.groupby(["station_id", "day"], observed=True)["low"]
                 .agg(low="sum", n="count").reset_index())

    s_local = samples["src_time"].dt.tz_convert("Asia/Taipei")
    t_local = (samples["src_time"] + pd.Timedelta(minutes=HORIZON_MIN)).dt.tz_convert("Asia/Taipei")
    t_day = t_local.dt.normalize().dt.tz_localize(None)
    lookup = pd.DataFrame({
        "station_id": samples["station_id"].to_numpy(),
        "day": s_local.dt.normalize().dt.tz_localize(None).to_numpy(),   # 截止日看「現在」的日期
        "hour": t_local.dt.hour.astype("int8").to_numpy(),              # 時段看「答案時刻」
        "offday": t_day.map({d: is_offday(d) for d in t_day.unique()}).to_numpy(),
    })

    out = pd.DataFrame(index=samples.index)
    out["low_rate_all"], _ = _past_days_rate(daily_all, lookup, ["station_id"])
    out["low_rate_slot"], out["slot_days"] = _past_days_rate(
        daily_slot, lookup, ["station_id", "offday", "hour"])

    st = stations.set_index("station_id")
    sid = samples["station_id"]
    out["is_campus"] = sid.map(st["district"]).eq("臺大公館校區").to_numpy()
    out["lat"] = sid.map(st["lat"]).astype("float32").to_numpy()
    out["lon"] = sid.map(st["lon"]).astype("float32").to_numpy()
    return out


# ---------------------------------------------------------------------------
# 組裝
# ---------------------------------------------------------------------------

def build_features(samples, history, stations=None, citywide=None, weather=None):
    """
    把各組特徵接到樣本上，回傳「樣本 + 特徵欄位」的 DataFrame（列的順序跟 samples 一樣）。

    samples   dataset.make_labels() 的結果（有答案的那些列）
    history   load_snapshots() 的完整結果（算「過去」的特徵要用）
    stations  load_stations() 的結果（站的行政區、經緯度）
    citywide  citywide.load_citywide() 的結果（全台北每個時刻的總數）
    weather   weather.load_weather(station_ids=DAAN_STATIONS) 的結果
    後三個是 None 就自己讀（讀到 history 的最後一天為止）。
    """
    last_day = history["date"].max()
    if stations is None:
        from youbike.data import load_stations
        stations = load_stations()
    if citywide is None:
        from youbike.citywide import load_citywide
        citywide = load_citywide(end=last_day, verbose=False)
    if weather is None:
        from youbike.weather import DAAN_STATIONS, load_weather
        weather = load_weather(end=last_day, station_ids=DAAN_STATIONS)

    st = station_features(history)
    keep = [c for c in FEATURE_GROUPS["station"] if c not in samples.columns]
    out = samples.merge(st[["station_id", "src_time"] + keep],
                        on=["station_id", "src_time"], how="left", validate="one_to_one")
    if len(out) != len(samples):
        raise AssertionError("接特徵之後列數變了，(站, 時間) 可能有重複")

    # 3-3：答案時刻的時間特徵（只跟時間有關，直接從每一列的時間算）
    target_time = out["src_time"] + pd.Timedelta(minutes=HORIZON_MIN)
    cal = calendar_features(target_time)
    out[CALENDAR_FEATURES] = cal[CALENDAR_FEATURES]

    # 3-4：站的類型
    prof = profile_features(history, out, stations)
    out[FEATURE_GROUPS["profile"]] = prof[FEATURE_GROUPS["profile"]]

    # 3-5：全台北的狀態（同一個時刻）
    city = citywide_features(out["src_time"], citywide)
    out[CITYWIDE_FEATURES] = city[CITYWIDE_FEATURES]

    # 3-6：天氣（t 那一刻已經拿到的最新觀測）
    wf = weather_features(out["src_time"], weather)
    out[WEATHER_FEATURES] = wf[WEATHER_FEATURES]
    return out
