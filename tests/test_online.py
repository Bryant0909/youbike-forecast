"""
phase 4-2：線上特徵 = 離線特徵 的檢查。

上線時每 5 分鐘只讀最近 8 天的快照、最近 2 天的全市總數和天氣，
站的歷史比例則查每天存好的計數表。這個測試確認：同一個時刻，
這樣算出來的特徵跟「用完整歷史」離線算出來的一模一樣（training-serving skew 的防線）。
"""

import pandas as pd
import pytest

from test_features import all_features, fake_history, fake_stations, fake_weather
from youbike.citywide import citywide_totals
from youbike.features import daily_low_counts, feature_columns
from youbike.online import CONTEXT_DAYS, SNAPSHOT_DAYS, latest_features

TZ = "Asia/Taipei"


@pytest.fixture(scope="module")
def data():
    # 10 天的人造資料，要預測的時刻放在最後一天的傍晚（7 天前、前一天都有資料）
    history = fake_history(days=10)
    weather = fake_weather(days=10)
    return history, weather


def online_at(history, weather, t, snapshot_days=SNAPSHOT_DAYS):
    """模擬上線：只給「t 以前、最近幾天」的資料，加上 t 那天以前的計數表。"""
    day = t.tz_convert(TZ).normalize()
    local_day = history["src_time"].dt.tz_convert(TZ).dt.normalize()
    recent = history[(local_day >= day - pd.Timedelta(days=snapshot_days - 1)) & (history["src_time"] <= t)]
    ctx = history[(local_day >= day - pd.Timedelta(days=CONTEXT_DAYS - 1)) & (history["src_time"] <= t)]
    past = history[local_day < day]                       # 計數表只涵蓋「t 那天以前」
    w_day = weather["obs_time"].dt.tz_convert(TZ).dt.normalize()
    return latest_features(recent, fake_stations(), citywide_totals(ctx),
                           weather[w_day >= day - pd.Timedelta(days=CONTEXT_DAYS - 1)],
                           daily_low_counts(past), at=t)


def pick_time(history, when):
    """取最接近 when（台北時間）的那一個快照時刻。"""
    target = pd.Timestamp(when, tz=TZ)
    times = pd.Series(history["src_time"].unique())
    return times.iloc[(times - target).abs().argmin()]


@pytest.mark.parametrize("when", ["2026-10-10 18:02", "2026-10-10 00:07", "2026-10-09 23:58"])
def test_online_equals_offline(data, when):
    history, weather = data
    t = pick_time(history, when)
    offline = all_features(history, weather)
    off = offline[offline["src_time"] == t].set_index("station_id")[feature_columns()].sort_index()
    on = online_at(history, weather, t).set_index("station_id")[feature_columns()].sort_index()
    assert len(on) == len(off) > 0
    pd.testing.assert_frame_equal(on, off, check_dtype=False)


def test_window_too_short_is_caught(data):
    # 反過來確認測試有用：如果上線時只讀 2 天快照，「上週同一時刻」就算不出來，測試應該要失敗
    history, weather = data
    t = pick_time(history, "2026-10-10 18:02")
    offline = all_features(history, weather)
    off = offline[offline["src_time"] == t].set_index("station_id")[feature_columns()].sort_index()
    on = online_at(history, weather, t, snapshot_days=2).set_index("station_id")[feature_columns()].sort_index()
    with pytest.raises(AssertionError):
        pd.testing.assert_frame_equal(on, off, check_dtype=False)
