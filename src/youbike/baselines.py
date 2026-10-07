"""
phase 2-4 ~ 2-5：兩個 baseline（評估基準）。

┌─ 為什麼要先做 baseline ────────────────────────────────────────────┐
│ 一個 PR-AUC 0.6 的模型是好還是不好？單看數字沒辦法知道。            │
│ 如果「什麼都不學、直接看現在有幾台車」就有 0.55，那 0.6 只是小進步；│
│ 如果 baseline 只有 0.2，那 0.6 就很厲害。                            │
│ 之後任何模型都要跟這兩個比 —— 贏不了它們的模型沒有存在的價值。      │
└────────────────────────────────────────────────────────────────────┘

兩個 baseline 都輸出「風險分數」（越高越可能借不到），交給 evaluate.py 評分。
"""

import pandas as pd


def persistence_score(samples):
    """
    Baseline ①「維持現狀」：30 分鐘後的狀態 ≈ 現在的狀態。

    分數 = −現在的可借車輛數。現在 0 台的站分數最高（最可能借不到），
    現在 20 台的站分數很低。

    為什麼這個 baseline 很難打敗：30 分鐘其實很短，大部分的站
    現在有車、30 分鐘後也還有車。模型真正要贏的地方是
    「現在還有 3 台，但這是下班尖峰，30 分鐘後會被借光」這種情況。
    """
    return -samples["bikes_available"].astype(float)


def historical_average_score(train, samples, slot_minutes=30, verbose=True):
    """
    Baseline ②「歷史同時段平均」：過去同一個站、同一種日子（平日／週末）、
    同一個時段，借不到車的比例是多少。

    分數 = 訓練期間，這個站在「答案時刻那個時段」y 為 True 的比例。
    例如「大安站、平日、18:00-18:30，過去 40% 的時候借不到」-> 0.4

    注意看的是「30 分鐘後」那個時段（target_*），不是現在的時段 ——
    我們要預測的是 30 分鐘後的狀態。

    只用 train 算歷史 —— 用到驗證或測試期的答案就等於偷看。

    【資料不夠時往上一層退】
    才幾天的資料，某些「站 × 日子類型 × 時段」的格子可能是空的
    （例如訓練期只有一個週末）。這時依序退回：
        站 × 日子類型 × 時段 -> 站 × 時段 -> 站 -> 全部的平均
    每一層都比「直接給 0」合理，也不會因為缺一格就沒有分數。
    """
    def keys(df):
        slot = (df["target_hour"].astype(int) * 60 + df["target_minute"].astype(int)) // slot_minutes
        return pd.DataFrame({
            "station_id": df["station_id"].values,
            "is_weekend": df["target_is_weekend"].values,
            "slot": slot.values,
        }, index=df.index)

    tk = keys(train).assign(y=train["y"].astype(float).values)
    sk = keys(samples)

    levels = [
        ["station_id", "is_weekend", "slot"],
        ["station_id", "slot"],
        ["station_id"],
    ]
    score = pd.Series(float("nan"), index=samples.index)
    used = {}
    for cols in levels:
        table = tk.groupby(cols)["y"].mean().rename("rate").reset_index()
        filled = sk.merge(table, on=cols, how="left")["rate"].values
        todo = score.isna()
        score[todo] = filled[todo.values]
        used[" × ".join(cols)] = int(todo.sum() - score.isna().sum())
    n_global = int(score.isna().sum())
    score = score.fillna(tk["y"].mean())
    used["全部的平均"] = n_global

    if verbose:
        total = len(samples)
        print("歷史同時段平均：各層用到的比例 " + "、".join(
            "{} {:.1%}".format(k, v / total) for k, v in used.items() if total))
    return score
