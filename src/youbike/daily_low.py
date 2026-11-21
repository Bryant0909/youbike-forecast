"""
phase 4-3：每天存一次的「站的歷史計數表」（profiles/daily_low.parquet）。

┌─ 為什麼需要這張表 ─────────────────────────────────────────────────┐
│ 「這站在過去的日子裡多常 <= 1 台」（low_rate_all、low_rate_slot）    │
│ 要用到**所有**過去的日子。上線時每 5 分鐘都重讀一次全部歷史太慢，   │
│ 而且會越來越慢（資料每天都在長）。                                   │
│                                                                    │
│ 但這兩個特徵其實只需要「每站、每天、每小時 <= 1 台幾次、總共幾次」   │
│ 這張小表（features.daily_low_counts）：一天約 6,700 列，一年才 250 萬 │
│ 列，讀起來不到一秒。所以每過完一天就把那天的計數補進表裡，             │
│ 預測時只要查表。                                                     │
└────────────────────────────────────────────────────────────────────┘

誰來補：predict.py 每次執行時先呼叫 update_daily_low()。
平常什麼都不用做（表已經是最新的）；過了午夜後的第一次執行，
會發現「昨天」還不在表裡，就讀昨天的快照算好補進去。
只補「今天以前」的日子 —— 今天還沒過完，而且預測本來就不會用到今天的計數。
"""

import os

import pandas as pd

from youbike.data import MODEL_DISTRICTS, available_days, default_data_dir, load_snapshots
from youbike.features import daily_low_counts

REL_PATH = os.path.join("profiles", "daily_low.parquet")


def daily_low_path(data_dir=None):
    return os.path.join(data_dir or default_data_dir(), REL_PATH)


def load_daily_low(data_dir=None):
    """讀出計數表；還沒有這個檔就回傳空表（欄位齊全）。"""
    path = daily_low_path(data_dir)
    if not os.path.isfile(path):
        return pd.DataFrame({"station_id": pd.Series(dtype=object),
                             "day": pd.Series(dtype="datetime64[ms]"),
                             "offday": pd.Series(dtype=bool), "hour": pd.Series(dtype="int8"),
                             "low": pd.Series(dtype="int64"), "n": pd.Series(dtype="int64")})
    return pd.read_parquet(path)


def update_daily_low(data_dir=None, today=None, verbose=True):
    """
    把「today 以前、還不在表裡」的日子補進計數表，存檔，回傳 (完整的表, 補了哪幾天)。

    today 預設是有資料的最後一天（用資料的日期，不用電腦的時鐘：
    在別的時區或資料延遲時都不會算錯）。
    """
    days = [d["date"] for d in available_days(data_dir)]
    today = today or days[-1]
    table = load_daily_low(data_dir)
    have = set(table["day"].dt.strftime("%Y-%m-%d")) if len(table) else set()
    missing = [d for d in days if d < today and d not in have]

    parts = [table] if len(table) else []
    for day in missing:
        history = load_snapshots(start=day, end=day, district=MODEL_DISTRICTS,
                                 data_dir=data_dir, verbose=False)
        counts = daily_low_counts(history)
        counts["day"] = counts["day"].astype("datetime64[ms]")
        parts.append(counts)
        if verbose:
            print("計數表：補上 {}（{} 列）".format(day, len(counts)), flush=True)

    if not missing:
        return table, []

    out = (pd.concat(parts, ignore_index=True)
           .sort_values(["day", "station_id", "hour"]).reset_index(drop=True))
    path = daily_low_path(data_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # 先寫到暫存檔再改名：寫到一半當掉的話，舊的表還在，不會留下壞掉的檔案
    tmp = path + ".tmp"
    out.to_parquet(tmp, index=False)
    os.replace(tmp, path)
    return out, missing
