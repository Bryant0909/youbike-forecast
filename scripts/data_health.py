"""
phase 1-2：資料健全性檢查 —— 在累積幾週資料之前，先確認收到的東西真的能用。

┌─ 這支程式跟 check_coverage.py 的差別 ──────────────────────────────┐
│ check_coverage.py 問的是「有沒有收到資料」（數量）                  │
│ 這支問的是「收到的資料有沒有意義」（品質）                          │
│                                                                    │
│ 為什麼要分開、而且要早做？                                          │
│   這個專案要累積 4~8 週的資料才能建模。如果資料本身有根本問題       │
│   （例如來源其實回傳的是快取的舊值、車輛數根本不會動），            │
│   現在發現 vs 八週後發現，差別是整個專案。                          │
│   數量正常但內容是死的 —— 這種失敗 check_coverage 完全看不出來。    │
└────────────────────────────────────────────────────────────────────┘

檢查七件事：
  1. 取樣間隔是不是真的 5 分鐘
  2. 資料延遲（我們抓到的時間 − 來源產生的時間）
  3. 相鄰時間點的車輛數【會不會變】  <- 最關鍵的一題
  4. 有沒有整天數值從沒變過的「死站」
  5. 「借不到車」在 =0 / <=1 / <=2 三種定義下的發生率
  6. 這個發生率在一天之中怎麼變化
  7. 它是普遍現象，還是被少數幾個長期空站灌出來的

資料每天都在長，所以這支要【定期重跑】—— 一天的資料只能看出「資料能不能用」，
看不出「尖峰在哪、哪些站最常空」那種結論，那需要幾週。

用法：
    python scripts/data_health.py                          # 全部資料
    python scripts/data_health.py --start 2026-10-01       # 指定起始日
    python scripts/data_health.py --district 大安區         # 只看一區（預設就是大安區）
"""

import argparse
import sys

import pandas as pd

from youbike.data import load_snapshots, DataNotFound

# 建模範圍已定案是大安區（見 docs/DECISIONS.md），所以預設看這一區。
DEFAULT_DISTRICT = "大安區"

# 「相鄰時間點完全沒變」的比例若高到這個程度，代表來源很可能給的是
# 快取的舊值 —— 那整批資料都沒有價值。這是最重要的一道防線。
SUSPICIOUS_UNCHANGED_RATE = 0.98


def section(title):
    print()
    print("=" * 62)
    print(title)
    print("=" * 62)


def check_interval(df):
    """1. 取樣間隔是不是真的 5 分鐘。"""
    section("1. 取樣間隔")
    t = pd.Series(sorted(df["src_time"].unique()))
    if len(t) < 2:
        print("  時間點太少，無法判斷")
        return
    gaps = t.diff().dt.total_seconds().div(60).dropna()
    print("  中位數 {:.1f} 分　平均 {:.1f} 分　最大 {:.1f} 分".format(
        gaps.median(), gaps.mean(), gaps.max()))
    near5 = ((gaps >= 4.5) & (gaps <= 5.5)).mean()
    print("  落在 4.5~5.5 分之間的比例：{:.1%}".format(near5))
    print("  {}".format("正常" if near5 >= 0.8 else "偏低 —— 取樣不規則，之後做時間特徵要小心"))


def check_lag(df):
    """2. 資料延遲 —— 這會影響上線後能拿到多新的資料。"""
    section("2. 資料延遲（我們抓到的時間 − 來源產生的時間）")
    lag = df.groupby("src_time")["lag_seconds"].first()
    print("  中位數 {:.0f} 秒　平均 {:.0f} 秒　最大 {:.0f} 秒".format(
        lag.median(), lag.mean(), lag.max()))
    print("  意思是：上線預測時，你手上最新的資料其實是約 {:.0f} 秒前的狀態。".format(lag.median()))
    print("  （做特徵和評估時要記得這件事，不然會高估模型在真實情境的表現）")


def check_movement(df):
    """3. 車輛數【會不會變】—— 整支程式最關鍵的一題。"""
    section("3. 相鄰時間點的車輛數變化")
    d = df.groupby("station_id")["bikes_available"].diff()
    valid = d.notna()
    unchanged = (d == 0).sum() / valid.sum()
    print("  完全沒變：{:.1%}　有變動：{:.1%}".format(unchanged, 1 - unchanged))
    print()
    print("  變化量分布（-3 ~ +3）：")
    vc = d.value_counts()
    for k in range(-3, 4):
        print("    {:+d} 台：{:>8,}".format(k, int(vc.get(float(k), 0))))

    print()
    if unchanged >= SUSPICIOUS_UNCHANGED_RATE:
        print("  ❌ 幾乎都沒在變 —— 來源很可能回傳的是快取的舊值，這批資料可能沒有價值！")
        return False
    print("  ✅ 車輛數確實在變動，資料是活的")
    return True


def check_dead_stations(df):
    """4. 整天數值從沒變過的站。"""
    section("4. 數值從沒變過的「死站」")
    per = df.groupby("station_id")["bikes_available"].nunique()
    dead = per[per == 1]
    print("  {} / {} 站（{:.1%}）".format(len(dead), len(per), len(dead) / len(per)))
    if len(dead):
        info = df.drop_duplicates("station_id").set_index("station_id")
        cols = [c for c in ("name", "district", "total_docks") if c in info.columns]
        show = info.loc[dead.index, cols].head(10)
        print(show.to_string())
        print("  （少數幾個是正常的 —— 可能是停用或沒人用的站；比例高才有問題）")


def check_target_rate(df, district):
    """5+6. 「借不到車」的發生率，以及它在一天之中怎麼變化。"""
    section("5. 「借不到車」的發生率（{}）".format(district))
    print("  這會決定 phase 2 的預測目標怎麼定。")
    print()
    for k in (0, 1, 2):
        r = (df["bikes_available"] <= k).mean()
        note = ""
        if r < 0.03:
            note = "  ← 正樣本太少，模型很難學"
        elif r > 0.4:
            note = "  ← 正樣本太多，預測價值低"
        print("    可借車輛 <= {}：{:6.2%}{}".format(k, r, note))
    print()
    print("  參考（還不了車）：")
    for k in (0, 1, 2):
        print("    可還空位 <= {}：{:6.2%}".format(k, (df["docks_available"] <= k).mean()))

    section("6. 「可借 <= 1」在一天之中的變化")
    h = df.groupby("hour").apply(
        lambda g: (g["bikes_available"] <= 1).mean(), include_groups=False)
    for hr, v in h.items():
        print("  {:02d}:00  {:5.1%} {}".format(hr, v, "█" * int(v * 100)))
    if len(h) < 24:
        print("  （只有 {} 個小時有資料，等累積完整幾天後再看才有意義）".format(len(h)))


def check_concentration(df, district):
    """
    7. 缺車是普遍現象還是集中在少數站。

    為什麼重要：如果大部分「缺車時刻」都來自少數幾個長期空著的站，
    那模型只要記住站名就能拿到好分數 —— 看起來準，實際上什麼都沒學到。
    """
    section("7. 缺車是普遍的還是集中在少數站（{}）".format(district))
    r = df.groupby("station_id").apply(
        lambda g: (g["bikes_available"] <= 1).mean(), include_groups=False)
    print("  從來沒缺過車　　 ：{:3d} 站（{:.0%}）".format((r == 0).sum(), (r == 0).mean()))
    print("  偶爾缺（0~20%） ：{:3d} 站".format(((r > 0) & (r <= 0.2)).sum()))
    print("  常常缺（20~80%）：{:3d} 站  ← 預測最有價值的就是這群".format(
        ((r > 0.2) & (r <= 0.8)).sum()))
    print("  幾乎都空（>80%）：{:3d} 站".format((r > 0.8).sum()))

    top10 = r.sort_values(ascending=False).head(10).index
    total_short = (df["bikes_available"] <= 1).sum()
    if total_short:
        share = (df[df["station_id"].isin(top10)]["bikes_available"] <= 1).sum() / total_short
        print()
        print("  前 10 個站佔了所有「缺車時刻」的 {:.0%}".format(share))
        print("  {}".format(
            "⚠️ 高度集中 —— 模型可能只要記住站名就有好分數，要特別小心評估方式"
            if share > 0.5 else
            "✅ 夠分散 —— 預測目標不是退化的"))

    if "name" in df.columns:
        print()
        print("  最容易借不到車的 8 個站：")
        names = df.drop_duplicates("station_id").set_index("station_id")["name"]
        for sid, v in r.sort_values(ascending=False).head(8).items():
            print("    {:5.0%}  {}".format(v, names.get(sid, sid)))


def main():
    ap = argparse.ArgumentParser(description="資料健全性檢查")
    ap.add_argument("--start", help="起始日期 YYYY-MM-DD")
    ap.add_argument("--end", help="結束日期 YYYY-MM-DD")
    ap.add_argument("--district", default=DEFAULT_DISTRICT,
                    help="要分析的行政區（預設 {}）".format(DEFAULT_DISTRICT))
    args = ap.parse_args()

    # 前四項檢查用全台北的資料（樣本多，判斷資料品質比較準），
    # 後三項只看建模範圍那一區（因為目標發生率是跟建模直接相關的）。
    print("載入全台北資料…")
    allcity = load_snapshots(start=args.start, end=args.end,
                             with_station_info=True, verbose=False)
    print("  {:,} 列、{} 站、{} 個時間點、{} 天".format(
        len(allcity), allcity["station_id"].nunique(),
        allcity["src_time"].nunique(), allcity["date"].nunique()))

    check_interval(allcity)
    check_lag(allcity)
    alive = check_movement(allcity)
    check_dead_stations(allcity)

    sub = allcity[allcity["district"] == args.district]
    if sub.empty:
        print("\n找不到行政區 {}，跳過後面三項".format(args.district))
    else:
        print()
        print("（以下只看 {}：{:,} 列、{} 站）".format(
            args.district, len(sub), sub["station_id"].nunique()))
        check_target_rate(sub, args.district)
        check_concentration(sub, args.district)

    section("結論")
    n_days = allcity["date"].nunique()
    if not alive:
        print("  ❌ 資料可能有根本問題，先不要繼續累積，查清楚再說")
        return 1
    print("  ✅ 資料是活的、可以用")
    if n_days < 14:
        print("  ⏳ 但目前只有 {} 天 —— 上面關於「尖峰在哪、哪些站最常空」的數字".format(n_days))
        print("     只能當參考，不能當結論。建模前至少要 4~8 週，")
        print("     而且要涵蓋平日和週末。等資料長了再重跑這支程式。")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except DataNotFound as e:
        print("找不到資料：{}".format(e))
        sys.exit(1)
