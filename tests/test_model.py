"""phase 3-7 模型流程的檢查（人造資料，幾秒鐘跑完）。"""

import numpy as np
import pandas as pd

from youbike.model import feature_importance, predict, to_matrix, train


def fake_xy(n=4000, seed=0):
    # 答案主要由 a 決定、b 有一點關係、c 完全無關 —— 模型應該學到 a 最重要
    rng = np.random.default_rng(seed)
    df = pd.DataFrame({"a": rng.normal(size=n), "b": rng.normal(size=n), "c": rng.normal(size=n),
                       "flag": rng.random(n) < 0.5})
    logit = 2.5 * df["a"] + 0.5 * df["b"]
    df["y"] = rng.random(n) < 1 / (1 + np.exp(-logit))
    df.loc[df.sample(frac=0.05, random_state=seed).index, "b"] = np.nan   # 缺值也要能吃
    return df


def test_to_matrix_converts_bool_and_keeps_nan():
    m = to_matrix(fake_xy(), ["a", "b", "flag"])
    assert m.dtypes.eq("float32").all()
    assert set(m["flag"].unique()) <= {0.0, 1.0}
    assert m["b"].isna().any()


def test_train_predict_importance():
    feats = ["a", "b", "c", "flag"]
    model = train(fake_xy(seed=0), fake_xy(seed=1), feats, params={"n_estimators": 200}, verbose=False)
    test = fake_xy(seed=2)
    score = predict(model, test, feats)
    assert score.between(0, 1).all() and score.index.equals(test.index)
    # 學到東西：答案為真的樣本，平均分數明顯比較高
    assert score[test["y"]].mean() > score[~test["y"]].mean() + 0.3
    imp = feature_importance(model, feats)
    assert imp.index[0] == "a" and abs(imp.sum() - 1) < 1e-9
