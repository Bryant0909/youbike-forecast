"""
phase 4-2：上線時的特徵 —— 只算「最新一刻」。

┌─ 上線最常出錯的地方：訓練時和上線時的特徵算法不一樣 ──────────────┐
│ 離線訓練時，特徵是拿整段歷史一次算完；上線時每 5 分鐘只有「現在」   │
│ 這一刻要預測。如果上線時另外寫一套比較省事的算法（例如少讀幾天、     │
│ 換一種方式算平均），模型看到的數字就跟訓練時不一樣 ——               │
│ 它不會報錯，只會默默變不準。業界把這叫 training-serving skew。       │
│                                                                    │
│ 這裡的做法：上線時直接呼叫離線那一套 build_features()，              │
│ 只是餵進去的是「夠用就好」的最近幾天資料：                           │
│   - 快照：最近 8 天（7 天前同一時刻要往回看 7 天又 30 分鐘）          │
│   - 全市總數、天氣：最近 2 天（最多往回看 24 小時）                   │
│   - 站的歷史比例：每天存好的計數表（daily_low.py），涵蓋所有過去的日子│
│ tests/test_online.py 檢查：同一個時刻，線上算出來的特徵必須跟         │
│ 離線（用完整歷史）算出來的一模一樣。                                 │
└────────────────────────────────────────────────────────────────────┘
"""

import pandas as pd

from youbike.features import build_features

# 快照要讀最近幾天（含今天）。7 天前的答案時刻 = t + 30 分 − 7 天，
# 再加上 ±2.5 分鐘的容許範圍，最多會用到 7 天前的前一天，所以讀 8 天。
SNAPSHOT_DAYS = 8
# 全市總數、天氣要讀最近幾天（含今天）：最多往回看 24 小時，讀今天和昨天就夠
CONTEXT_DAYS = 2


def latest_features(history, stations, citywide, weather, daily_low, at=None):
    """
    算出某一刻（預設是 history 裡最新的時刻）每一站的特徵。

    history    最近 SNAPSHOT_DAYS 天的快照（load_snapshots 的結果，只要建模範圍的站）
    stations   load_stations() 的結果
    citywide   最近 CONTEXT_DAYS 天的全市總數（load_citywide 的結果）
    weather    最近 CONTEXT_DAYS 天的天氣（load_weather 的結果）
    daily_low  每天存好的計數表（daily_low.load_daily_low），要涵蓋 t 那天以前的所有日子
    at         要算哪一刻；None = 最新一刻。只會用到 at 以前的資料。

    回傳 DataFrame：每站一列，有 station_id、src_time、bikes_available 等欄位和全部特徵。
    """
    t = history["src_time"].max() if at is None else at
    h = history[history["src_time"] <= t]
    # 這一刻的每一站就是要預測的「樣本」（上線時沒有答案，所以沒有 y）
    samples = h[h["src_time"] == t][["station_id", "src_time", "date"]].reset_index(drop=True)
    if samples.empty:
        raise ValueError("{} 這一刻沒有任何站的資料".format(t))

    c = citywide[citywide["src_time"] <= t]
    w = None if weather is None else weather[weather["fetched_at"] <= t]
    return build_features(samples, h, stations=stations, citywide=c, weather=w, daily_low=daily_low)


def load_online_inputs(data_dir=None, end=None):
    """
    從資料夾讀出上線預測需要的所有東西（除了 daily_low，見 daily_low.py）。
    end = 最後一天（預設是有資料的最後一天）。回傳 dict。
    """
    from youbike.citywide import load_citywide
    from youbike.data import MODEL_DISTRICTS, available_days, load_snapshots, load_stations
    from youbike.weather import DAAN_STATIONS, load_weather

    from youbike.data import DataNotFound

    end = end or available_days(data_dir)[-1]["date"]
    day = pd.Timestamp(end)
    snap_start = (day - pd.Timedelta(days=SNAPSHOT_DAYS - 1)).strftime("%Y-%m-%d")
    ctx_start = (day - pd.Timedelta(days=CONTEXT_DAYS - 1)).strftime("%Y-%m-%d")
    try:
        weather = load_weather(start=ctx_start, end=end, station_ids=DAAN_STATIONS, data_dir=data_dir)
    except (DataNotFound, FileNotFoundError):
        # 天氣是附加資料：最近沒資料（DataNotFound）或整個資料夾不見（FileNotFoundError），
        # 都讓天氣特徵變成缺值，預測照常進行
        print("注意：最近沒有天氣資料，天氣特徵當成缺值")
        weather = None
    return {
        "history": load_snapshots(start=snap_start, end=end, district=MODEL_DISTRICTS,
                                  data_dir=data_dir, verbose=False),
        "stations": load_stations(data_dir),
        "citywide": load_citywide(start=ctx_start, end=end, data_dir=data_dir, verbose=False),
        "weather": weather,
    }
