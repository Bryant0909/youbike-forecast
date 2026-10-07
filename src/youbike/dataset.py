"""
phase 2-1 ~ 2-2：把快照資料變成「可以拿來訓練／評估」的樣本，並照時間切開。

┌─ 一筆樣本長什麼樣子 ───────────────────────────────────────────────┐
│ 一筆樣本 = 「某個站、某個時間點 t」：                               │
│   輸入：t 那一刻（以及更早）知道的事 —— 車輛數、幾點、星期幾、天氣  │
│   答案：t + 30 分鐘時，這個站的可借車輛 <= 門檻（例如 <= 1）嗎？     │
│                                                                    │
│ 注意「答案」是從同一份資料裡往後看 30 分鐘得到的 —— 我們不需要      │
│ 另外收集標籤，原始資料存的是車輛數，門檻隨時可以改了重算。           │
└────────────────────────────────────────────────────────────────────┘

┌─ 資料有空洞怎麼辦（不需要刪資料）─────────────────────────────────┐
│ 規則：t + 30 分鐘「前後 2.5 分鐘內」要有這個站的資料，才有答案。    │
│ 沒有的話，這個 t 就不當樣本 —— 只跳過受影響的那幾筆，空洞以外的     │
│ 資料完全照用。實測 9/30~10/6 收到的時間點有 95.8% 可以用，           │
│ 連 10/6 那種有 334 分鐘大洞的日子都還有九成以上。                    │
│                                                                    │
│ 為什麼是 ±2.5 分鐘？取樣間隔是 5 分鐘，±2.5 剛好是「最近的那一筆」， │
│ 又不會抓到隔壁那一輪。取樣時間本身會晃 ±1 分鐘左右，這個寬度夠用。  │
└────────────────────────────────────────────────────────────────────┘

用法：

    from youbike.data import load_snapshots
    from youbike.dataset import make_labels, time_split

    df = load_snapshots(district="大安區")
    samples = make_labels(df, threshold=1)
    train, valid, test = time_split(samples, valid_days=["2026-10-05"],
                                    test_days=["2026-10-06", "2026-10-07"])
"""

import pandas as pd

HORIZON_MIN = 30        # 預測多久之後
TOLERANCE_MIN = 2.5     # t + 30 分鐘前後多少分鐘內要有資料


def make_labels(df, threshold=1, horizon_min=HORIZON_MIN,
                tolerance_min=TOLERANCE_MIN, verbose=True):
    """
    幫每一列（站, t）配上 t + horizon 分鐘時的車輛數與答案。

    參數：
        df          load_snapshots() 的結果
        threshold   「借不到車」的定義：可借車輛 <= threshold。
                    預測目標還沒定案（見 docs/DECISIONS.md），所以做成參數。
        horizon_min 預測多久之後（預設 30 分鐘）

    回傳 df 的子集合（只留有答案的列），多出：
        label_time      答案那一筆的實際時間（UTC）
        bikes_future    t + 30 分鐘時的可借車輛數
        y               答案：bikes_future <= threshold（True = 會借不到車）
        target_hour, target_minute, target_weekday, target_is_weekend
                        答案那個時刻的台北時間資訊 ——
                        「歷史同時段平均」要看的是「30 分鐘後」那個時段，不是現在
    """
    horizon = pd.Timedelta(minutes=horizon_min)
    tol = pd.Timedelta(minutes=tolerance_min)

    left = df.copy()
    # 我們想要的答案時間。astype 是因為 Parquet 存的時間是毫秒精度，
    # 加上 Timedelta 之後 pandas 會變成奈秒，merge_asof 要求兩邊型別完全一樣。
    left["_want"] = (left["src_time"] + horizon).astype(left["src_time"].dtype)

    right = df[["station_id", "src_time", "bikes_available"]].rename(
        columns={"src_time": "label_time", "bikes_available": "bikes_future"})

    # merge_asof：對每一列，在同一站（by）裡找「時間最接近 _want」的那一列，
    # 差距超過 tolerance 就當作找不到。它要求兩邊都照時間欄位排好序。
    merged = pd.merge_asof(
        left.sort_values("_want"), right.sort_values("label_time"),
        left_on="_want", right_on="label_time", by="station_id",
        direction="nearest", tolerance=tol,
    )

    has_label = merged["bikes_future"].notna()
    if verbose:
        n, k = len(merged), int(has_label.sum())
        print("建標籤：{:,} 列裡有 {:,} 列找得到 {} 分鐘後的資料（{:.1f}%），其餘跳過".format(
            n, k, horizon_min, k / n * 100 if n else 0))

    out = merged[has_label].drop(columns="_want")
    out["bikes_future"] = out["bikes_future"].astype("int16")
    out["y"] = out["bikes_future"] <= threshold

    target_local = (out["src_time"] + horizon).dt.tz_convert("Asia/Taipei")
    out["target_hour"] = target_local.dt.hour.astype("int8")
    out["target_minute"] = target_local.dt.minute.astype("int8")
    out["target_weekday"] = target_local.dt.weekday.astype("int8")
    out["target_is_weekend"] = out["target_weekday"] >= 5

    return out.sort_values(["station_id", "src_time"]).reset_index(drop=True)


def time_split(samples, valid_days, test_days, verbose=True):
    """
    照時間切成 訓練 / 驗證 / 測試 三段 —— 絕對不能隨機打亂。

    【為什麼不能隨機切】
    同一個站 10:00 和 10:05 的狀態幾乎一模一樣。隨機切的話，10:00 在訓練、
    10:05 在測試，模型等於偷看了答案，分數會好看得很假。
    真實使用時我們永遠是「用過去預測未來」，評估也必須這樣切。

    【交界處的緩衝（purge）】
    訓練集最後一筆是 10/4 23:55，它的答案在 10/5 00:25 —— 已經是驗證期的資料了。
    所以「答案時間」跨進下一段的樣本要丟掉，不然訓練時就偷看到了驗證期。

    參數：
        valid_days, test_days   日期字串清單（台北時間），例如 ["2026-10-05"]。
                                比 valid 更早的全部是訓練集。

    回傳 (train, valid, test) 三個 DataFrame。
    """
    valid_days, test_days = sorted(valid_days), sorted(test_days)
    if valid_days and test_days and valid_days[-1] >= test_days[0]:
        raise ValueError("驗證期必須整段在測試期之前")

    first_eval_day = (valid_days or test_days)[0]
    train = samples[samples["date"] < first_eval_day]
    valid = samples[samples["date"].isin(valid_days)]
    test = samples[samples["date"].isin(test_days)]

    def purge(part, next_day):
        # 答案時間落在下一段（>= 下一段第一天 00:00 台北時間）的樣本丟掉
        if part.empty or next_day is None:
            return part
        boundary = pd.Timestamp(next_day, tz="Asia/Taipei")
        return part[part["label_time"] < boundary]

    train = purge(train, first_eval_day)
    valid = purge(valid, test_days[0] if test_days else None)

    if verbose:
        for name, part in (("訓練", train), ("驗證", valid), ("測試", test)):
            if part.empty:
                print("{}：（空）".format(name))
                continue
            print("{}：{} ~ {}，{:,} 筆，借不到車的比例 {:.1%}".format(
                name, part["date"].min(), part["date"].max(), len(part), part["y"].mean()))
    return train, valid, test
