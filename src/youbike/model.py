"""
phase 3-7：LightGBM 模型。

┌─ LightGBM 是什麼（白話版）─────────────────────────────────────────┐
│ 它是一大堆「決策樹」排隊接力：                                       │
│   第 1 棵樹先粗略地猜（例如「現在 <= 1 台的站，30 分鐘後多半也沒車」）│
│   第 2 棵樹專門修正第 1 棵猜錯的地方（「…但如果是早上 6 點，         │
│            調度車快來了，其實會有車」）                              │
│   第 3 棵再修正前兩棵加起來還猜錯的地方 … 一路疊幾百棵。             │
│ 每棵樹都很小、很笨，但疊起來就能學到「現在車數 × 時段 × 站的類型」   │
│ 這種交互作用 —— 這正是兩個 baseline 各自都學不到的東西。             │
└────────────────────────────────────────────────────────────────────┘

┌─ 要疊幾棵樹？用驗證集決定（early stopping）─────────────────────────┐
│ 樹疊越多，訓練集的分數一定越好，但到某個點之後就是在「背答案」，    │
│ 換到沒看過的日子反而變差（過度擬合，overfitting）。                 │
│ 所以每多疊幾棵就在驗證集（訓練期之後的日子）上打一次分數，           │
│ 連續 100 棵都沒有進步就停，留下最好的那個數量。                      │
│ 測試集從頭到尾都不參與任何決定 —— 它只在最後打一次分數。            │
└────────────────────────────────────────────────────────────────────┘
"""

import pandas as pd

# 超參數（模型的「設定」）。刻意選保守的常見值、不在測試集上調：
# 資料只有幾週，在同一份資料上反覆調參數，很容易調出「只對這幾週有效」的設定。
PARAMS = {
    "objective": "binary",
    "learning_rate": 0.05,       # 每棵樹修正的幅度；小一點比較穩，代價是要多疊幾棵
    "num_leaves": 63,            # 每棵樹最多分成幾格
    "min_child_samples": 200,    # 每一格至少要有幾筆樣本 —— 防止為了極少數的情況切出一格
    "subsample": 0.8,            # 每棵樹隨機只看 8 成的樣本、
    "subsample_freq": 1,
    "colsample_bytree": 0.8,     # 和 8 成的特徵：讓每棵樹不一樣，比較不容易背答案
    "reg_lambda": 1.0,
    "n_estimators": 3000,        # 上限；實際用幾棵由 early stopping 決定
    "verbose": -1,
}
EARLY_STOPPING_ROUNDS = 100


def to_matrix(df, features):
    """特徵表 -> 模型吃的數字矩陣。布林值轉成 0/1，缺值保留成 NaN（LightGBM 會自己處理）。"""
    X = df[features].copy()
    for c in X.columns:
        if X[c].dtype == bool:
            X[c] = X[c].astype("int8")
    return X.astype("float32")


def train(train_df, valid_df, features, params=None, seed=0, verbose=True):
    """
    訓練一個 LightGBM，用 valid_df 決定要疊幾棵樹。回傳訓練好的模型。

    評分標準用 average_precision（也就是 PR-AUC，跟成績單同一個指標）。
    """
    import lightgbm as lgb

    p = dict(PARAMS, **(params or {}), random_state=seed)
    model = lgb.LGBMClassifier(**p)
    model.fit(
        to_matrix(train_df, features), train_df["y"].astype(int),
        eval_X=(to_matrix(valid_df, features),), eval_y=(valid_df["y"].astype(int),),
        eval_metric="average_precision",
        callbacks=[lgb.early_stopping(EARLY_STOPPING_ROUNDS, verbose=False)],
    )
    if verbose:
        print("LightGBM：用了 {} 棵樹（上限 {}），驗證集 PR-AUC {:.3f}".format(
            model.best_iteration_, p["n_estimators"],
            model.best_score_["valid_0"]["average_precision"]))
    return model


def predict(model, df, features):
    """風險分數（0~1，越高越可能借不到），回傳跟 df 同 index 的 Series。"""
    return pd.Series(model.predict_proba(to_matrix(df, features))[:, 1], index=df.index)


def feature_importance(model, features):
    """
    每個特徵有多重要：用 gain（這個特徵在所有樹裡總共讓預測進步了多少）。
    比「被用了幾次」可靠 —— 有些特徵常被拿來做小修小補，次數多但貢獻小。
    回傳由大到小排好、加總為 1 的 Series。
    """
    gain = model.booster_.feature_importance(importance_type="gain")
    s = pd.Series(gain, index=features, dtype=float)
    return (s / s.sum()).sort_values(ascending=False)
