"""phase 4-3：每天存一次的計數表（profiles/daily_low.parquet）的檢查。"""

import os

import pandas as pd

from test_features import fake_history, fake_stations
from youbike.daily_low import load_daily_low, update_daily_low
from youbike.features import daily_low_counts


def write_data_dir(root, history, days):
    """把人造快照寫成跟 data 分支一樣的結構：snapshots/<日期>.parquet + stations.parquet。"""
    os.makedirs(os.path.join(root, "snapshots"), exist_ok=True)
    st = fake_stations().assign(name=lambda d: d["station_id"], address="")
    st.to_parquet(os.path.join(root, "stations.parquet"), index=False)
    for day in days:
        part = history[history["date"] == day].drop(columns=["date"])
        part.to_parquet(os.path.join(root, "snapshots", day + ".parquet"), index=False)


def key(df):
    return df.assign(day=df["day"].astype("datetime64[ms]")).sort_values(
        ["day", "station_id", "hour"]).reset_index(drop=True)[["station_id", "day", "offday", "hour", "low", "n"]]


def test_build_then_append(tmp_path):
    history = fake_history(days=6)
    days = sorted(history["date"].unique())          # 包含 9/30（時間晃動的那一筆）
    root = str(tmp_path)

    # 第一次：資料到第 5 天，表裡要有「第 5 天以前」的每一天
    write_data_dir(root, history, days[:-1])
    table, added = update_daily_low(root, verbose=False)
    assert added == days[:-2]                         # 最後一天（today）不補
    expect = daily_low_counts(history[history["date"].isin(days[:-2])])
    pd.testing.assert_frame_equal(key(table), key(expect), check_dtype=False)

    # 第二次：沒有新的日子 -> 什麼都不補
    _, added = update_daily_low(root, verbose=False)
    assert added == []

    # 多收了一天：只補新過完的那一天，而且存檔後讀得回來
    write_data_dir(root, history, days[-1:])
    table, added = update_daily_low(root, verbose=False)
    assert added == [days[-2]]
    expect = daily_low_counts(history[history["date"].isin(days[:-1])])
    pd.testing.assert_frame_equal(key(load_daily_low(root)), key(expect), check_dtype=False)
