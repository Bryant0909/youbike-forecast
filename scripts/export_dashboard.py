"""
phase 1-3：把 EDA 的結果匯出成網頁（Dashboard）用的資料表。

┌─ 這支程式在做什麼 ─────────────────────────────────────────────────┐
│ notebooks/01_eda.ipynb 是給「人」看的：程式、圖、解說都在裡面。       │
│ 網頁版（claude.ai 上的 Dashboard）則需要一份一份的資料表（JSON），   │
│ 網頁上的每個數字、每張圖都從這些表算出來，點數字就能看到它的來源。   │
│                                                                    │
│ 這支程式用跟 notebook 一模一樣的算法，把結果存成 10 份 JSON。       │
│ 資料變多之後（例如滿 4 週），重跑這一支、再把檔案交給 Claude          │
│ 上傳到網頁，網頁就更新了 —— 不用改任何程式。                        │
└────────────────────────────────────────────────────────────────────┘

輸出的 10 份表（都在 --out 指定的資料夾）：
    daily            每天的比例、全區總車數、雨量、圖上的標註
    heatmap          星期幾 × 小時（不含國定假日）
    tod              一天裡每 15 分鐘一格，上班日 / 休息日
    hourly_district  兩個區分開，每小時的比例與總車數
    stations         每站的空站率、車柱數、位置
    spells           每一段「完全沒車」持續多久
    by_now           現在幾台 → 30 分鐘後 <= 1 的機率
    by_change        現在 2~4 台時，最近 15 分鐘的變化 → 30 分鐘後 <= 1 的機率
    rain_hourly      上班日每小時，下雨中 vs 沒下雨
    summary          網頁上引用的單一數字（一列一個指標）

用法：
    python scripts/export_dashboard.py                              # 10/1 到最後一個完整的日子
    python scripts/export_dashboard.py --end 2026-10-28             # 指定結束日
    python scripts/export_dashboard.py --out data/processed/dash2   # 換輸出位置

注意：本機的 Python 環境目前偶爾會在 pandas 裡當掉（Windows 存取違規，
exit code -1073741819），跟這支程式的邏輯無關，重跑一次就好。
"""

import argparse
import json
import os
import warnings
from datetime import datetime

import numpy as np
import pandas as pd

from youbike.data import MODEL_DISTRICTS, TAIPEI_TZ, available_days, load_snapshots
from youbike.dataset import make_labels
from youbike.weather import DAAN_STATIONS, load_weather, rain_increment

# numpy 對 pandas 內部的時間運算會丟 DeprecationWarning，跟我們的程式無關，先關掉免得洗版
warnings.filterwarnings("ignore", category=DeprecationWarning)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_OUT = os.path.join(REPO_ROOT, "data", "processed", "dashboard")

# 9/30 下午才開始收，只有半天，所以預設從 10/1 開始
DEFAULT_START = "2026-10-01"

# 國定假日（跟 notebook 一樣）。國慶日 10/10 是週六，前一天 10/9 補假。
# 之後資料跨到其他假日時要加進來；phase 3 做假日特徵時會換成完整的行事曆。
HOLIDAYS = {"2026-10-09": "補假"}

# 一天的雨量超過這個值，圖上就標「大雨」。
# 這是口語的「大雨」，不是氣象署的定義（氣象署的大雨是 24 小時 80 mm 以上）。
HEAVY_RAIN_MM = 20

WEEKDAY_ZH = ["一", "二", "三", "四", "五", "六", "日"]


def day_type(date_str):
    """上班日 / 休息日（週末 + 國定假日）。"""
    if date_str in HOLIDAYS or pd.Timestamp(date_str).weekday() >= 5:
        return "休息日"
    return "上班日"


def last_full_day():
    """
    有資料的日子裡，最後一個「已經過完」的日子。
    今天的資料還在一直長，拿來算每天的比例會偏掉，所以預設不算今天。
    """
    today = datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d")
    days = [d["date"] for d in available_days() if d["date"] < today]
    if not days:
        raise SystemExit("找不到任何已經過完的日子")
    return days[-1]


def save(out_dir, name, rows):
    """存成 JSON。numpy 的數字型別 JSON 不認得，先轉成一般的 Python 數字；小數留 4 位。"""
    def clean(v):
        if isinstance(v, (np.floating, float)):
            return None if np.isnan(v) else round(float(v), 4)
        if isinstance(v, np.integer):
            return int(v)
        if isinstance(v, np.bool_):
            return bool(v)
        return v

    rows = [{k: clean(v) for k, v in r.items()} for r in rows]
    path = os.path.join(out_dir, name + ".json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, separators=(",", ":"))
    print("  {:<16} {:>4} 列  {:>7,} bytes".format(name, len(rows), os.path.getsize(path)))


def main():
    ap = argparse.ArgumentParser(description="把 EDA 結果匯出成網頁用的 JSON")
    ap.add_argument("--start", default=DEFAULT_START, help="起始日（預設 {}）".format(DEFAULT_START))
    ap.add_argument("--end", help="結束日（預設：最後一個已經過完的日子）")
    ap.add_argument("--out", default=DEFAULT_OUT, help="輸出資料夾（預設 data/processed/dashboard）")
    args = ap.parse_args()
    end = args.end or last_full_day()
    os.makedirs(args.out, exist_ok=True)

    print("讀取建模範圍（{}）{} ~ {} 的資料…".format(" + ".join(MODEL_DISTRICTS), args.start, end))
    df = load_snapshots(start=args.start, end=end, district=MODEL_DISTRICTS,
                        with_station_info=True, verbose=False)
    df["day_type"] = df["date"].map({d: day_type(d) for d in df["date"].unique()})
    df["empty0"] = df["bikes_available"] <= 0
    df["empty1"] = df["bikes_available"] <= 1
    print("  {:,} 列、{} 站、{} 天".format(len(df), df["station_id"].nunique(), df["date"].nunique()))
    print("匯出到 {}".format(args.out))

    # --- 天氣：兩個測站的 10 分鐘雨量取平均，再加總成「最近 30 分鐘」---
    w = rain_increment(load_weather(start=args.start, end=end, station_ids=DAAN_STATIONS))
    rain10 = w.groupby(w["obs_time"].dt.floor("10min"))["rain_mm"].mean()
    rain30 = rain10.rolling("30min").sum()
    # 快照時間「往下」取整到 10 分鐘再對照，不會用到之後才發布的觀測（那是未來的資訊）
    df["rain30"] = df["src_time"].dt.floor("10min").map(rain30)
    df["raining"] = df["rain30"] > 0
    rain_day = (w.assign(date=w["time_taipei"].dt.strftime("%Y-%m-%d"))
                  .groupby(["date", "station_id"])["rain_mm"].sum().groupby("date").mean())

    # --- daily：每天的比例、全區總車數、雨量 ---
    daily = df.groupby("date").agg(empty0=("empty0", "mean"), empty1=("empty1", "mean"),
                                   n_points=("src_time", "nunique"))
    # 全區總車數：每個時間點把所有站的可借車輛加起來，再取當天平均
    daily["total_bikes"] = (df.groupby(["date", "src_time"])["bikes_available"].sum()
                              .groupby("date").mean().round(0))

    def note(d):
        # 圖上的標註：假日優先，其次是大雨
        if d in HOLIDAYS:
            return HOLIDAYS[d]
        return "大雨" if rain_day.get(d, 0) >= HEAVY_RAIN_MM else ""

    save(args.out, "daily", [
        {"date": d, "weekday": "週" + WEEKDAY_ZH[pd.Timestamp(d).weekday()], "day_type": day_type(d),
         "note": note(d), "empty1": r.empty1, "empty0": r.empty0,
         "total_bikes": int(r.total_bikes), "n_points": int(r.n_points),
         "rain_mm": float(rain_day.get(d, 0.0))}
        for d, r in daily.iterrows()])

    # --- heatmap：星期幾 × 小時。國定假日不算，不然會把那個星期幾拉低 ---
    heat = (df[~df["date"].isin(HOLIDAYS)].groupby(["weekday", "hour"])
              .agg(empty1=("empty1", "mean"), n_days=("date", "nunique")))
    save(args.out, "heatmap", [
        {"weekday": "週" + WEEKDAY_ZH[wd], "weekday_num": int(wd), "hour": int(h),
         "empty1": r.empty1, "n_days": int(r.n_days)}
        for (wd, h), r in heat.iterrows()])

    # --- tod：一天裡每 15 分鐘一格 ---
    df["slot15"] = df["hour"].astype(int) * 60 + (df["minute"] // 15) * 15
    tod = df.groupby(["day_type", "slot15"])["empty1"].mean()
    save(args.out, "tod", [
        {"day_type": t, "minute_of_day": int(m), "time": "{}:{:02d}".format(m // 60, m % 60), "empty1": v}
        for (t, m), v in tod.items()])

    # --- hourly_district：兩個區分開看，每小時的比例與總車數 ---
    hd = df.groupby(["district", "day_type", "hour"]).agg(empty1=("empty1", "mean"))
    hd["total_bikes"] = (df.groupby(["district", "day_type", "date", "src_time", "hour"])["bikes_available"].sum()
                           .groupby(["district", "day_type", "hour"]).mean().round(0))
    save(args.out, "hourly_district", [
        {"district": dd, "day_type": t, "hour": int(h), "empty1": r.empty1, "total_bikes": int(r.total_bikes)}
        for (dd, t, h), r in hd.iterrows()])

    # --- stations：每站的空站率 ---
    st = (df.groupby("station_id")
            .agg(name=("name", "first"), district=("district", "first"),
                 lat=("lat", "first"), lon=("lon", "first"),
                 docks=("total_docks", "median"),
                 empty0=("empty0", "mean"), empty1=("empty1", "mean"))
            .sort_values("empty0", ascending=False))
    st["name"] = st["name"].str.replace("YouBike2.0_", "", regex=False)
    # 網頁的站點表用站名分辨每一列，重複的話會對錯列
    assert st["name"].is_unique, "有重複的站名，網頁的站點表會對錯列"
    save(args.out, "stations", [
        {"station_id": sid, "name": r["name"], "district": r["district"], "docks": int(r.docks),
         "empty0": r.empty0, "empty1": r.empty1, "lat": r.lat, "lon": r.lon}
        for sid, r in st.iterrows()])

    # --- spells：每一段「完全沒車」持續多久 ---
    # 同一站連續好幾筆都是 0 台算一段。中間斷超過 7.5 分鐘（= 漏了一筆以上）就切開，
    # 免得把資料空洞誤認成「一直沒車」。
    d = df.sort_values(["station_id", "src_time"]).reset_index(drop=True)
    gap = d.groupby("station_id")["src_time"].diff().dt.total_seconds() / 60
    cut = (d["station_id"] != d["station_id"].shift()) | (gap > 7.5) | (d["empty0"] != d["empty0"].shift())
    spell_id = cut.cumsum()
    e = d["empty0"]
    sp = d[e].groupby(spell_id[e]).agg(start=("src_time", "first"), end=("src_time", "last"))
    minutes = (sp["end"] - sp["start"]).dt.total_seconds() / 60 + 5   # 只有 1 筆的算 5 分鐘
    bucket = (np.round(minutes.clip(upper=120) / 5) * 5).astype(int)   # 四捨五入到 5 分鐘；120 以上併一格
    hist = (pd.DataFrame({"b": bucket, "m": minutes}).groupby("b")
              .agg(spells=("m", "size"), total=("m", "sum")))
    save(args.out, "spells", [
        {"minutes": int(b), "label": "120+" if b == 120 else str(b),
         "spells": int(r.spells), "empty_minutes": r.total}
        for b, r in hist.iterrows()])

    # --- by_now：現在幾台 → 30 分鐘後 <= 1 的機率 ---
    lab = make_labels(df, threshold=1, verbose=False)
    now = lab["bikes_available"].clip(upper=12)   # 12 台以上併成一組
    g = lab.groupby(now)["y"].agg(["mean", "size"])
    save(args.out, "by_now", [
        {"now": int(k), "label": "12+" if k == 12 else str(k),
         "group": "現在已經 <= 1" if k <= 1 else "現在 >= 2",
         "p_future_empty1": r["mean"], "samples": int(r["size"])}
        for k, r in g.iterrows()])

    # --- by_change：現在 2~4 台時，最近 15 分鐘的變化 ---
    past = df[["station_id", "src_time", "bikes_available"]].rename(
        columns={"src_time": "t_past", "bikes_available": "bikes_15min_ago"})
    lab["_want"] = (lab["src_time"] - pd.Timedelta(minutes=15)).dt.as_unit("ms")
    lab = pd.merge_asof(lab.sort_values("_want"), past.sort_values("t_past"),
                        left_on="_want", right_on="t_past", by="station_id",
                        direction="nearest", tolerance=pd.Timedelta(minutes=2.5))
    lab["change15"] = (lab["bikes_available"] - lab["bikes_15min_ago"]).clip(-5, 5)
    mid = lab[lab["bikes_available"].between(2, 4) & lab["bikes_15min_ago"].notna()]
    gc = mid.groupby("change15")["y"].agg(["mean", "size"])
    save(args.out, "by_change", [
        {"change15": int(k), "label": "-5 以下" if k == -5 else "{:+d}".format(int(k)),
         "p_future_empty1": r["mean"], "samples": int(r["size"])}
        for k, r in gc.iterrows()])

    # --- rain_hourly：上班日每小時，下雨中 vs 沒下雨 ---
    work = df[(df["day_type"] == "上班日") & df["rain30"].notna()]
    gr = work.groupby(["hour", "raining"]).agg(empty1=("empty1", "mean"), n_points=("src_time", "nunique"))
    save(args.out, "rain_hourly", [
        {"hour": int(h), "weather": "下雨中" if raining else "沒下雨",
         "empty1": r.empty1, "n_points": int(r.n_points)}
        for (h, raining), r in gr.iterrows()])

    # --- summary：網頁上引用的單一數字 ---
    by_type = df.groupby("day_type")[["empty0", "empty1"]].mean()
    by_dist = df[df["day_type"] == "上班日"].groupby("district")["empty1"].mean()
    n_by = df.drop_duplicates("station_id")["district"].value_counts()
    work_hd = hd.xs("上班日", level="day_type")["empty1"]
    curve_work = tod["上班日"]
    share = st["empty0"].cumsum() / st["empty0"].sum()
    n = len(st)
    now_low = lab["bikes_available"] <= 1
    slots = df.drop_duplicates("src_time")
    rain_split = work.groupby("raining")["empty1"].mean()

    summary = [
        ("n_stations", "站數", df["station_id"].nunique()),
        ("n_daan", "大安區站數", int(n_by.get("大安區", 0))),
        ("n_campus", "臺大公館校區站數", int(n_by.get("臺大公館校區", 0))),
        ("n_days", "天數", df["date"].nunique()),
        ("n_rows", "資料列數", len(df)),
        ("rate_work_e1", "上班日 可借 <= 1 的比例", by_type.loc["上班日", "empty1"]),
        ("rate_off_e1", "休息日 可借 <= 1 的比例", by_type.loc["休息日", "empty1"]),
        ("rate_work_e0", "上班日 可借 <= 0 的比例", by_type.loc["上班日", "empty0"]),
        ("rate_off_e0", "休息日 可借 <= 0 的比例", by_type.loc["休息日", "empty0"]),
        ("rate_work_daan", "上班日 大安區 可借 <= 1", by_dist["大安區"]),
        ("rate_work_campus", "上班日 臺大公館校區 可借 <= 1", by_dist["臺大公館校區"]),
        ("daan_morning", "上班日 大安區 10 點 可借 <= 1", work_hd[("大安區", 10)]),
        ("campus_night", "上班日 校區 22 點 可借 <= 1", work_hd[("臺大公館校區", 22)]),
        ("campus_afternoon", "上班日 校區 15 點 可借 <= 1", work_hd[("臺大公館校區", 15)]),
        ("peak_work", "上班日最高的比例（15 分鐘一格）", curve_work.max()),
        ("min_work", "上班日最低的比例（15 分鐘一格）", curve_work.min()),
        ("top10_share", "最常空的前 10% 站佔沒車時間", share.iloc[int(n * .1) - 1]),
        ("top20_share", "最常空的前 20% 站佔沒車時間", share.iloc[int(n * .2) - 1]),
        ("top50_share", "最常空的前 50% 站佔沒車時間", share.iloc[int(n * .5) - 1]),
        ("never_empty", "從沒空過的站數", int((st["empty0"] == 0).sum())),
        ("corr_docks", "車柱數與沒車比例的相關係數", st["docks"].corr(st["empty0"])),
        ("spell_count", "完全沒車的段數", len(minutes)),
        ("spell_median", "每段持續時間中位數（分鐘）", minutes.median()),
        ("spell_le10", "10 分鐘內恢復的段數比例", (minutes <= 10).mean()),
        ("spell_time_ge30", "沒車時間落在 30 分鐘以上長段的比例", minutes[minutes >= 30].sum() / minutes.sum()),
        ("base_rate", "30 分鐘後 <= 1 的比例", lab["y"].mean()),
        ("persist_precision", "維持現狀的精確率", lab.loc[now_low, "y"].mean()),
        ("persist_recall", "維持現狀的召回率", now_low[lab["y"]].mean()),
        ("new_share", "借不到的情況裡現在還有 >= 2 台的比例", 1 - now_low[lab["y"]].mean()),
        ("p_from_ge2", "現在 >= 2 台、30 分鐘後 <= 1 的機率", lab.loc[~now_low, "y"].mean()),
        ("mid_mean", "現在 2~4 台的整體平均", mid["y"].mean()),
        ("rain_points_share", "下雨中的時間點比例", slots["raining"].mean()),
        ("rain_rate_dry", "上班日 沒下雨時 可借 <= 1", rain_split.get(False, np.nan)),
        ("rain_rate_wet", "上班日 下雨中 可借 <= 1", rain_split.get(True, np.nan)),
        ("rain_days", "有下雨的天數", int((rain_day > 0).sum())),
    ]
    save(args.out, "summary", [{"metric": k, "label": lb, "value": v} for k, lb, v in summary])
    print("完成。把 {} 裡的檔案交給 Claude 上傳到網頁就會更新。".format(args.out))


if __name__ == "__main__":
    main()
