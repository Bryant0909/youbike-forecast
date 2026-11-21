"""
phase 3-5：全台北「現在總共有多少車可借」。

┌─ 為什麼一個大安區的模型要看全台北 ─────────────────────────────────┐
│ phase 1-4 發現 10/13 那週全台北的總車數少了約 9%（大安區少 17%），  │
│ 借不到車的比例整週都偏高 —— 原因從站點資料分辨不出來（路上的車變多？│
│ 收去維修？），但「全市現在有多少車」這個數字能直接反映系統的狀態。  │
│ 我們本來就收全台北的資料（收集範圍 ≠ 建模範圍），這裡就用上了。     │
└────────────────────────────────────────────────────────────────────┘

同一次抓取裡所有站的 src_time 都一樣（實測差 0 秒），所以全市總數
以 src_time 為鍵，直接接回同一時刻的樣本 —— 用的是「同一個時刻」的資料，不是未來。

全台北一天約 52 萬列，一次讀一個月會用掉好幾 GB 記憶體，
所以一天一天讀、只讀需要的欄位、讀完立刻加總成「每個時刻一筆」。
"""

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from youbike.data import available_days

COLUMNS = ["station_id", "src_time", "bikes_available", "total_docks", "is_active"]


def citywide_totals(df):
    """
    把一堆站的快照加總成「每個時刻一筆」。df 要有 COLUMNS 這幾欄（任何站都可以）。

    回傳欄位：
        src_time
        city_bikes      營運中的站加起來總共幾台可借
        city_ratio      city_bikes / 總車柱數（站數會慢慢增加，用比例比較不受影響）
        city_low_frac   營運中的站裡，<= 1 台的站佔幾成
        city_n          這個時刻有幾站有資料（用來找出 API 只回傳一部分的時刻）
    """
    # 同一個 (站, 時刻) 只算一次：日檔和小檔同時存在時（壓縮做到一半）會重複，
    # 不先去掉的話那個時刻的全市車數會變成兩倍
    d = df[df["is_active"] == 1].drop_duplicates(["station_id", "src_time"])
    g = d.assign(low=(d["bikes_available"] <= 1).astype("int32")).groupby("src_time")
    out = pd.DataFrame({
        "city_bikes": g["bikes_available"].sum().astype("float32"),
        "city_docks": g["total_docks"].sum().astype("float32"),
        "city_low_frac": g["low"].mean().astype("float32"),
        "city_n": g["low"].size().astype("int32"),
    })
    out["city_ratio"] = (out["city_bikes"] / out["city_docks"]).astype("float32")
    return out.drop(columns="city_docks").reset_index()


def load_citywide(end=None, data_dir=None, verbose=True, start=None):
    """
    一天一天讀全台北的快照，回傳 citywide_totals() 的結果（start ~ end，含頭含尾）。
    上線預測時只需要最近兩天（start = 昨天）：60 分鐘前的總數、
    判斷「站數不完整」用的過去 24 小時中位數，都不會超過這個範圍。
    """
    parts = []
    for day in available_days(data_dir):
        if (end and day["date"] > end) or (start and day["date"] < start):
            continue
        frames = [pq.read_table(p, columns=COLUMNS).to_pandas() for p in day["paths"]]
        parts.append(citywide_totals(pd.concat(frames, ignore_index=True)))
        if verbose:
            print("全市總數：{} 完成".format(day["date"]), flush=True)
    out = pd.concat(parts, ignore_index=True)
    # 每天各自加總，跨日的時刻不會重複；保險起見還是檢查一次
    if out["src_time"].duplicated().any():
        raise AssertionError("全市總數裡有重複的時刻")
    return mark_incomplete(out.sort_values("src_time").reset_index(drop=True), verbose)


# 站數不到「平常」的這個比例，就當作 API 只回傳了一部分
MIN_STATION_FRACTION = 0.95


def mark_incomplete(citywide, verbose=True):
    """
    API 偶爾只回傳一部分的站（實測 10/14 17:27 只有 1155 站，平常約 1790 站），
    那一刻的全市總車數就會少算一大截 —— 模型會以為「全市突然少了一半的車」。
    這種時刻的全市特徵一律當成「不知道」（NaN），而不是硬塞一個錯的數字。
    「平常」= 過去 24 小時（含這一刻）站數的中位數 —— 只看過去，連這種小地方也不偷看未來。
    """
    out = citywide.sort_values("src_time").reset_index(drop=True)
    usual = out.set_index("src_time")["city_n"].rolling("1D", min_periods=1).median().to_numpy()
    bad = out["city_n"] < MIN_STATION_FRACTION * usual
    if verbose and bad.any():
        print("全市總數：{} 個時刻的站數不完整，當作缺值".format(int(bad.sum())))
    out.loc[bad, ["city_bikes", "city_ratio", "city_low_frac"]] = np.nan
    return out


CITYWIDE_FEATURES = ["city_bikes", "city_ratio", "city_low_frac", "city_bikes_diff60"]


def citywide_features(sample_times, citywide):
    """
    把全市總數接到樣本上（用同一個 src_time）。

        city_bikes, city_ratio, city_low_frac   t 當下的全市狀態
        city_bikes_diff60   跟 60 分鐘前比，全市可借車輛的變化
                            （早上尖峰全市會一路掉、半夜調度車補回來）

    回傳 DataFrame，跟 sample_times 同順序。
    """
    c = citywide.sort_values("src_time")
    # 注意：時間欄位用 reset_index 保留 pandas 的型別。用 .to_numpy() 會把毫秒精度、
    # 有時區的時間變成奈秒，merge 時兩邊型別不同就會報錯
    left = pd.DataFrame({"src_time": sample_times.astype(c["src_time"].dtype).reset_index(drop=True),
                         "_pos": np.arange(len(sample_times))})
    now = left.merge(c, on="src_time", how="left").sort_values("_pos")

    # 60 分鐘前：在前後 2.5 分鐘內找最近的一筆（跟其他 lag 特徵一樣的規則）
    want = pd.DataFrame({"_want": (sample_times - pd.Timedelta(minutes=60)).astype(c["src_time"].dtype)
                                  .reset_index(drop=True),
                         "_pos": np.arange(len(sample_times))})
    ago = pd.merge_asof(want.sort_values("_want"), c[["src_time", "city_bikes"]].sort_values("src_time"),
                        left_on="_want", right_on="src_time", direction="nearest",
                        tolerance=pd.Timedelta(minutes=2.5)).sort_values("_pos")

    out = now[["city_bikes", "city_ratio", "city_low_frac"]].reset_index(drop=True)
    out["city_bikes_diff60"] = (out["city_bikes"].to_numpy() - ago["city_bikes"].to_numpy()).astype("float32")
    out.index = sample_times.index
    return out
