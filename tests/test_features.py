"""
phase 3 特徵的檢查。跑法：python -m pytest tests/

最重要的一個檢查是 test_no_peeking_into_future：
    「把某個時間點 T 之後的資料全部刪掉再算一次，T 以前的特徵必須一模一樣。」
如果某個特徵偷偷用到了未來的資料，刪掉未來之後它的值就會變 —— 這個測試就會失敗。
這比一個一個特徵用眼睛檢查可靠，而且之後新增特徵也自動會被檢查到。

用的是人造資料（不需要真的資料、幾秒鐘跑完），刻意做出取樣時間晃動和一段空洞，
跟真實資料會遇到的情況一樣。
"""

import numpy as np
import pandas as pd
import pytest

from youbike.citywide import citywide_totals
from youbike.dataset import make_labels
from youbike.features import FEATURE_GROUPS, build_features, feature_columns, station_features


def fake_history(n_stations=3, days=9, seed=0, gap=("2026-10-05 10:00", "2026-10-05 16:00")):
    """
    人造快照：每 5 分鐘一筆、時間晃動 ±40 秒、中間挖掉一段空洞。
    跟真實資料一樣，同一次抓取裡所有站的 src_time 相同（晃動是整批一起晃）。
    """
    rng = np.random.default_rng(seed)
    times = pd.date_range("2026-10-01", periods=days * 288, freq="5min", tz="Asia/Taipei")
    jitter = pd.to_timedelta(rng.integers(-40, 41, len(times)), unit="s")
    src = (times + jitter).tz_convert("UTC").astype("datetime64[ms, UTC]")
    rows = []
    for s in range(n_stations):
        docks = 20 + 10 * s
        bikes = np.clip(np.cumsum(rng.integers(-2, 3, len(times))) % (docks + 1), 0, docks)
        rows.append(pd.DataFrame({
            "station_id": "S{}".format(s),
            "src_time": src,
            "fetched_at": src + pd.Timedelta(seconds=75),
            "bikes_available": bikes.astype("int16"),
            "total_docks": np.int16(docks),
            "is_active": np.int8(1),
        }))
    df = pd.concat(rows, ignore_index=True)
    df["docks_available"] = (df["total_docks"] - df["bikes_available"]).astype("int16")
    local = df["src_time"].dt.tz_convert("Asia/Taipei")
    df["date"] = local.dt.strftime("%Y-%m-%d")
    lo, hi = (pd.Timestamp(t, tz="Asia/Taipei") for t in gap)
    df = df[(local < lo) | (local >= hi)]
    return df.sort_values(["station_id", "src_time"]).reset_index(drop=True)


def fake_stations(n_stations=3):
    return pd.DataFrame({
        "station_id": ["S{}".format(s) for s in range(n_stations)],
        "district": ["大安區", "臺大公館校區", "大安區"][:n_stations],
        "lat": [25.02, 25.017, 25.03][:n_stations],
        "lon": [121.54, 121.537, 121.55][:n_stations],
    })


def fake_weather(days=9, seed=1):
    """
    人造天氣：兩個測站每 10 分鐘一筆，precip_mm 跟真的一樣是「當天累積」，
    而且 10~16 分鐘後才「拿到」（fetched_at），跟真實的發布延遲一樣。
    """
    rng = np.random.default_rng(seed)
    obs = pd.date_range("2026-10-01", periods=days * 144, freq="10min", tz="Asia/Taipei")
    rows = []
    for sid in ("W1", "W2"):
        rain = np.where(rng.random(len(obs)) < 0.1, rng.random(len(obs)) * 3, 0.0)
        day = (obs - pd.Timedelta(minutes=1)).date
        acc = pd.Series(rain).groupby(day).cumsum().to_numpy()
        rows.append(pd.DataFrame({
            "station_id": sid,
            "obs_time": obs.tz_convert("UTC").astype("datetime64[ms, UTC]"),
            "fetched_at": (obs + pd.to_timedelta(rng.integers(600, 961, len(obs)), unit="s"))
                          .tz_convert("UTC").astype("datetime64[ms, UTC]"),
            "precip_mm": acc.astype("float32"),
            "temp_c": (25 + rng.normal(0, 1, len(obs))).astype("float32"),
        }))
    return pd.concat(rows, ignore_index=True)


@pytest.fixture(scope="module")
def history():
    return fake_history()


@pytest.fixture(scope="module")
def weather():
    return fake_weather()


def all_features(history, weather=None, samples=None):
    weather = fake_weather() if weather is None else weather
    if samples is None:
        samples = make_labels(history, threshold=1, verbose=False)
    return build_features(samples, history, stations=fake_stations(),
                          citywide=citywide_totals(history), weather=weather)


# 答案相關的欄位：算特徵時絕對不能用到
LABEL_COLUMNS = ["label_time", "bikes_future", "y"]


def test_no_peeking_into_future(history, weather):
    # 截在傍晚，而且故意不是整點：當天 18:02 以後的資料都刪掉。
    # 天氣是用「拿到的時間」截 —— 18:02 那一刻還沒拿到的觀測，就當作還不存在
    cutoff = pd.Timestamp("2026-10-07 18:02", tz="Asia/Taipei")
    samples = make_labels(history, threshold=1, verbose=False)
    full = all_features(history, weather, samples)

    # 刪掉未來的版本：樣本用「截止點以前的每一個時刻」，而且把答案欄位拿掉 ——
    # 【為什麼不重新建標籤】重新建標籤的話，截止點前 30 分鐘的樣本會因為
    # 「30 分鐘後沒資料」被丟掉，偷看「未來 30 分鐘以內」的特徵就檢查不到了
    # （實際發生過：天氣用觀測時間對齊，延遲只有 10~16 分鐘，第一版測試沒抓到）。
    # 拿掉答案欄位則是：如果有特徵偷用答案，這裡會直接報錯。
    early = samples[samples["src_time"] < cutoff].drop(columns=LABEL_COLUMNS)
    cut = all_features(history[history["src_time"] < cutoff],
                       weather[weather["fetched_at"] < cutoff], early)

    key = ["station_id", "src_time"]
    cols = key + feature_columns()
    a = full[full["src_time"] < cutoff][cols].set_index(key).sort_index()
    b = cut[cols].set_index(key).sort_index()
    assert len(a) == len(b) > 1000
    # 截止點前 30 分鐘內的樣本真的有被比到
    assert (b.index.get_level_values("src_time") > cutoff - pd.Timedelta(minutes=20)).any()
    pd.testing.assert_frame_equal(a, b, check_dtype=False)


def test_lag_and_diff_by_hand(history):
    f = station_features(history)
    one = f[f["station_id"] == "S1"].reset_index(drop=True)
    # 正常的一列（不在空洞旁邊）：5 分鐘前就是上一列
    i = 500
    assert one.loc[i, "bikes_lag5"] == one.loc[i - 1, "bikes_available"]
    assert one.loc[i, "diff5"] == one.loc[i, "bikes_available"] - one.loc[i - 1, "bikes_available"]
    # 60 分鐘前 = 往上 12 列
    assert one.loc[i, "bikes_lag60"] == one.loc[i - 12, "bikes_available"]
    # 最近 60 分鐘的最少 = 這一列 + 往上 11 列（前 12 列剛好落在 60 分鐘邊界上，看晃動）
    window = one.loc[i - 11:i, "bikes_available"]
    assert one.loc[i, "min_60m"] <= window.min()
    assert one.loc[i, "max_60m"] >= window.max()


def test_lag_missing_after_gap(history):
    # 空洞結束後的第一筆：5 分鐘前、30 分鐘前都在空洞裡，應該是 NaN 而不是亂接
    f = station_features(history)
    local = f["src_time"].dt.tz_convert("Asia/Taipei")
    first_after = f[local >= pd.Timestamp("2026-10-05 16:00", tz="Asia/Taipei")].groupby("station_id").head(1)
    assert first_after["bikes_lag5"].isna().all()
    assert first_after["bikes_lag30"].isna().all()


def test_min_since_low(history):
    f = station_features(history)
    low_now = f["bikes_available"] <= 1
    assert (f.loc[low_now, "min_since_low"] == 0).all()
    assert (f.loc[~low_now, "min_since_low"] > 0).all()


def test_build_features_keeps_rows(history):
    samples = make_labels(history, threshold=1, verbose=False)
    out = build_features(samples, history, stations=fake_stations())
    assert len(out) == len(samples)
    assert (out["y"].to_numpy() == samples["y"].to_numpy()).all()
    for c in feature_columns():
        assert c in out.columns


def test_profile_uses_only_past_days(history):
    f = all_features(history)
    local = f["src_time"].dt.tz_convert("Asia/Taipei")
    # 每站第一個有資料的日子沒有「過去的日子」 -> 沒有歷史比例
    # （不能直接寫 10/1：人造資料的時間會晃，第一筆可能落在 9/30 23:59）
    day = local.dt.strftime("%Y-%m-%d")
    first_day = day == day.groupby(f["station_id"]).transform("min")
    assert f.loc[first_day, "low_rate_all"].isna().all()
    assert (f.loc[first_day, "slot_days"] == 0).all()
    # 之後的日子：比例在 0~1 之間
    later = f.loc[~first_day, "low_rate_all"].dropna()
    assert len(later) and later.between(0, 1).all()

    # 手算一個：S1 在 10/4 的 low_rate_all = 10/1~10/3 這站 <= 1 台的比例
    h = history[history["station_id"] == "S1"]
    hd = h["src_time"].dt.tz_convert("Asia/Taipei").dt.strftime("%Y-%m-%d")
    expect = (h.loc[hd < "2026-10-04", "bikes_available"] <= 1).mean()
    got = f.loc[(f["station_id"] == "S1") & (local.dt.strftime("%Y-%m-%d") == "2026-10-04"), "low_rate_all"]
    assert got.nunique() == 1 and abs(got.iloc[0] - expect) < 1e-6
    assert f.loc[f["station_id"] == "S1", "is_campus"].all()


def test_citywide_same_moment(history):
    f = all_features(history)
    # 全市總數 = 同一時刻 3 站的可借車輛加總
    expect = history.groupby("src_time")["bikes_available"].sum()
    got = f.set_index("src_time")["city_bikes"]
    assert (got == expect.loc[got.index].to_numpy()).all()


def test_weather_uses_fetch_time_not_obs_time(history, weather):
    f = all_features(history, weather)
    # 手算一筆：某個樣本時刻 t，兩站各自「fetched_at <= t」的最新一筆氣溫，取平均
    row = f.iloc[3000]
    t = row["src_time"]
    temps = []
    for sid, g in weather.groupby("station_id"):
        seen = g[g["fetched_at"] <= t].sort_values("fetched_at")
        temps.append(seen["temp_c"].iloc[-1])
    assert abs(row["temp_c"] - np.mean(temps)) < 1e-4


def test_rain_window(weather):
    from youbike.weather import weather_features
    # 在某站剛拿到一筆觀測的時刻查，rain_60m 應該等於那站最近 6 筆的雨量增量加總（兩站平均）
    from youbike.weather import rain_increment
    w = rain_increment(weather)
    t = weather["fetched_at"].iloc[500] + pd.Timedelta(minutes=30)
    got = weather_features(pd.Series([t]), weather)["rain_60m"].iloc[0]
    sums = []
    for sid, g in w.groupby("station_id"):
        seen = g[g["fetched_at"] <= t].sort_values("obs_time")
        last = seen["obs_time"].iloc[-1]
        win = seen[seen["obs_time"] > last - pd.Timedelta(minutes=60)]
        sums.append(win["rain_mm"].sum())
    assert abs(got - np.mean(sums)) < 1e-4


def test_incomplete_snapshot_marked_missing(history):
    from youbike.citywide import mark_incomplete
    c = citywide_totals(history)
    c.loc[100, "city_n"] = 1     # 假裝這一刻 API 只回傳了 1 站
    m = mark_incomplete(c, verbose=False)
    assert np.isnan(m.loc[100, "city_bikes"])
    assert m["city_bikes"].isna().sum() == 1
