"""
phase 4-4：訓練「上線用」的模型，存到 models/。

跟 train_model.py（評估用）的差別：
    train_model.py       把最後幾天留下來當測試集 —— 目的是誠實地打分數
    train_production.py  分數已經在 phase 3 驗證過了，上線的模型要盡量用到最新的資料

做法分兩階段：
    1. 用「最後 4 天以外」訓練、最後 4 天當驗證集：決定要疊幾棵樹（early stopping），
       順便在驗證集上量 PR-AUC，並找出「精確率 70%」的警告門檻
    2. 用**全部**資料、同樣的樹數重新訓練一次 —— 這就是上線的模型
       （第 2 階段沒有可以打分數的資料了，所以分數記的是第 1 階段的）

輸出（commit 進 main，網頁和預測程式都讀這兩個檔）：
    models/lgbm_le1.txt       LightGBM 模型（文字檔）
    models/model_card.json    模型的「身分證」：版本、用哪段資料訓練、特徵清單、
                              驗證分數、警告門檻 —— 預測時會檢查特徵清單一不一樣

用法：
    python scripts/train_production.py               # 用到昨天為止的資料
    python scripts/train_production.py --end 2026-11-21
建議每個月重新訓練一次，用 PR 更新 models/（見 docs/DECISIONS.md 2026-11-22）。
"""

import argparse
import datetime
import json
import os

import numpy as np
import pandas as pd

from youbike.data import MODEL_DISTRICTS, REPO_ROOT, available_days, load_snapshots
from youbike.dataset import HORIZON_MIN, make_labels, time_split
from youbike.evaluate import average_precision, recall_at_precision
from youbike.features import build_features, feature_columns
from youbike.model import PARAMS, predict, to_matrix, train

MODEL_DIR = os.path.join(REPO_ROOT, "models")
THRESHOLD = 1            # 預測目標：30 分鐘後可借 <= 1（docs/DECISIONS.md 2026-11-03）
ALERT_PRECISION = 0.7    # 警告門檻：精確率 70%（docs/DECISIONS.md 2026-11-04）


def yesterday_taipei():
    return (pd.Timestamp.now(tz="Asia/Taipei") - pd.Timedelta(days=1)).strftime("%Y-%m-%d")


def alert_threshold(y, score, min_precision):
    """精確率至少 min_precision 的前提下、抓到最多的那個分數門檻。"""
    order = np.argsort(-np.asarray(score), kind="mergesort")
    ys = np.asarray(y, dtype=bool)[order]
    ss = np.asarray(score)[order]
    precision = np.cumsum(ys) / np.arange(1, len(ys) + 1)
    ok = np.where(precision >= min_precision)[0]
    if not len(ok):
        raise ValueError("驗證集上達不到精確率 {:.0%}".format(min_precision))
    return float(ss[ok.max()])


def main():
    import lightgbm as lgb

    ap = argparse.ArgumentParser(description="訓練上線用的模型，存到 models/")
    ap.add_argument("--end", default=yesterday_taipei(), help="用到這一天為止（含），預設昨天")
    ap.add_argument("--valid-days", type=int, default=4, help="第 1 階段的驗證天數（預設 4）")
    args = ap.parse_args()

    days = [d["date"] for d in available_days() if d["date"] <= args.end]
    valid_days = days[-args.valid_days:]
    features = feature_columns()

    print("讀資料、算特徵…", flush=True)
    history = load_snapshots(end=args.end, district=MODEL_DISTRICTS, verbose=False)
    X = build_features(make_labels(history, threshold=THRESHOLD, verbose=False), history)

    # --- 第 1 階段：決定樹的數量、量驗證分數、找警告門檻 ---
    train_df, valid_df, _ = time_split(X, valid_days, [], verbose=True)
    stage1 = train(train_df, valid_df, features, verbose=True)
    n_trees = int(stage1.best_iteration_)
    score = predict(stage1, valid_df, features)
    valid = {
        "days": [valid_days[0], valid_days[-1]],
        "base_rate": float(valid_df["y"].mean()),
        "pr_auc": average_precision(valid_df["y"], score),
        "recall_p70": recall_at_precision(valid_df["y"], score, ALERT_PRECISION),
    }
    thr = alert_threshold(valid_df["y"], score, ALERT_PRECISION)
    print("驗證集 PR-AUC {:.3f}、recall@P70 {:.3f}；警告門檻（精確率 70%）= 風險分數 >= {:.3f}".format(
        valid["pr_auc"], valid["recall_p70"], thr))

    # --- 第 2 階段：全部資料、同樣的樹數，重新訓練 ---
    print("用全部 {:,} 筆、{} 棵樹重新訓練…".format(len(X), n_trees), flush=True)
    final = lgb.LGBMClassifier(**dict(PARAMS, n_estimators=n_trees, random_state=0))
    final.fit(to_matrix(X, features), X["y"].astype(int))

    os.makedirs(MODEL_DIR, exist_ok=True)
    model_path = os.path.join(MODEL_DIR, "lgbm_le{}.txt".format(THRESHOLD))
    final.booster_.save_model(model_path)

    card = {
        "version": args.end,
        "created_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "target": "{} 分鐘後可借車輛 <= {}".format(HORIZON_MIN, THRESHOLD),
        "threshold": THRESHOLD,
        "horizon_min": HORIZON_MIN,
        "districts": MODEL_DISTRICTS,
        "data": {"start": days[0], "end": args.end, "n_samples": int(len(X)),
                 "n_stations": int(X["station_id"].nunique()), "base_rate": float(X["y"].mean())},
        "model_file": os.path.basename(model_path),
        "n_trees": n_trees,
        "params": {k: v for k, v in PARAMS.items() if k != "n_estimators"},
        "features": features,
        "valid": valid,
        "alert": {"precision_target": ALERT_PRECISION, "score_threshold": thr},
        "lightgbm_version": lgb.__version__,
    }
    with open(os.path.join(MODEL_DIR, "model_card.json"), "w", encoding="utf-8") as f:
        json.dump(card, f, ensure_ascii=False, indent=2)
    print("存好了：{}（{:.1f} MB）、model_card.json".format(
        model_path, os.path.getsize(model_path) / 2**20))


if __name__ == "__main__":
    main()
