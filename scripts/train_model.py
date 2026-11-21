"""
phase 3-7：訓練 LightGBM，跟兩個 baseline 印在同一張成績單上。

流程：讀資料 -> 建標籤 -> 算特徵（features.py）-> 照時間切 -> 訓練（驗證集決定樹的數量）
     -> 三個模型在驗證、測試期打分數（全天、尖峰、大安區、校區）

用法：
    python scripts/train_model.py                         # 預設：<= 1、最後 4 天測試、再往前 2 天驗證
    python scripts/train_model.py --threshold 0           # 輔助指標：<= 0
    python scripts/train_model.py --groups station calendar   # 只用某幾組特徵（看每組的貢獻）
    python scripts/train_model.py --folds                 # 3-8：三段不同的測試期各做一次

結果另存到 data/processed/model/（不上傳 git）：
    scorecard_<名稱>.csv     成績單
    importance_<名稱>.csv    特徵重要度
    test_pred_<名稱>.parquet 測試期每一筆的答案和三個模型的分數（3-9 錯誤分析用）
"""

import argparse
import os

import pandas as pd

from youbike.baselines import historical_average_score, persistence_score
from youbike.data import MODEL_DISTRICTS, REPO_ROOT, available_days, load_snapshots
from youbike.dataset import make_labels, time_split
from youbike.evaluate import is_peak, scorecard
from youbike.features import FEATURE_GROUPS, build_features, feature_columns
from youbike.model import feature_importance, predict, train

OUT_DIR = os.path.join(REPO_ROOT, "data", "processed", "model")
MIN_PRECISIONS = (0.6, 0.7, 0.8)

# 3-8：多段測試期（walk-forward）。每一段都只用「驗證期以前」的資料訓練，
# 跟真的上線時一樣：永遠用過去預測未來。
# 為什麼是這三段：
#   A  訓練資料只有 8 天，測試期有國慶連假（10/10~11）和 10/13 少車那週的開始
#   B  10/13 那週全市少約 9% 的車（phase 1-4），測試模型遇到「狀態不一樣的一週」
#   C  10/22~26 收集中斷之後；跟 2-6、3-7 的切法完全相同，可以直接比
FOLDS = {
    "A": (["2026-10-08", "2026-10-09"], ["2026-10-10", "2026-10-11", "2026-10-12", "2026-10-13"]),
    "B": (["2026-10-14", "2026-10-15"], ["2026-10-16", "2026-10-17", "2026-10-18", "2026-10-19"]),
    "C": (["2026-10-28", "2026-10-29"], ["2026-10-30", "2026-10-31", "2026-11-01", "2026-11-02"]),
}


def yesterday_taipei():
    now = pd.Timestamp.now(tz="Asia/Taipei")
    return (now - pd.Timedelta(days=1)).strftime("%Y-%m-%d")


def subsets(part):
    """成績單要分開看的子集合：EDA 發現兩區節奏相反，只看整體可能只在一區好。"""
    return {
        "全天": slice(None),
        "尖峰": is_peak(part),
        "大安區": ~part["is_campus"].astype(bool),
        "校區": part["is_campus"].astype(bool),
    }


def run_period(X, valid_days, test_days, features, seed=0, verbose=True):
    """
    一段「訓練 / 驗證 / 測試」的完整流程。回傳 (成績單 rows, 模型, 測試期的分數表)。
    3-8 的多段測試期就是用不同的日期呼叫這個函式好幾次。
    """
    train_df, valid_df, test_df = time_split(X, valid_days, test_days, verbose=verbose)
    model = train(train_df, valid_df, features, seed=seed, verbose=verbose)

    rows, test_scores = [], None
    for name, part in (("驗證", valid_df), ("測試", test_df)):
        scores = {
            "① 維持現狀": persistence_score(part),
            "② 歷史同時段平均": historical_average_score(train_df, part, verbose=False),
            "③ LightGBM": predict(model, part, features),
        }
        for r in scorecard(part, scores, subsets(part), MIN_PRECISIONS):
            rows.append({"資料": name, **r})
        if name == "測試":
            test_scores = part[["station_id", "src_time", "date", "target_hour", "is_campus",
                                "bikes_available", "bikes_future", "y"]].copy()
            test_scores["score_persist"] = scores["① 維持現狀"]
            test_scores["score_hist"] = scores["② 歷史同時段平均"]
            test_scores["score_lgbm"] = scores["③ LightGBM"]
    return rows, model, test_scores


def print_table(rows):
    table = pd.DataFrame(rows)
    show = table.copy()
    for col in show.columns:
        if col == "base rate" or col == "PR-AUC" or col.startswith("recall@"):
            show[col] = show[col].map("{:.3f}".format)
    try:
        import tabulate  # noqa: F401
        print(show.to_markdown(index=False))
    except ImportError:
        print(show.to_string(index=False))
    return table


def run_folds(X, features, name):
    """3-8：每一段各訓練一個模型，印出各段的測試成績和平均。"""
    rows = []
    for fold, (valid_days, test_days) in FOLDS.items():
        print("--- 第 {} 段：驗證 {} ~ {}、測試 {} ~ {} ---".format(
            fold, valid_days[0], valid_days[-1], test_days[0], test_days[-1]))
        fold_rows, _, _ = run_period(X, valid_days, test_days, features)
        rows += [{"段": fold, **r} for r in fold_rows if r["資料"] == "測試"]
        print()
    table = pd.DataFrame(rows).drop(columns="資料")
    os.makedirs(OUT_DIR, exist_ok=True)
    table.to_csv(os.path.join(OUT_DIR, "folds_{}.csv".format(name)), index=False)

    metrics = ["PR-AUC"] + ["recall@P{:.0f}".format(p * 100) for p in MIN_PRECISIONS]
    for metric in metrics:
        wide = table.pivot_table(index=["時段", "模型"], columns="段", values=metric, sort=False)
        wide["平均"] = wide.mean(axis=1)
        print("【{}】各段測試期".format(metric))
        print(wide.map("{:.3f}".format).to_string())
        print()
    base = table.pivot_table(index="時段", columns="段", values="base rate", aggfunc="first", sort=False)
    print("【發生率】各段測試期")
    print(base.map("{:.3f}".format).to_string())
    print()
    print("結果存到 {}".format(os.path.join(OUT_DIR, "folds_{}.csv".format(name))))


def main():
    ap = argparse.ArgumentParser(description="訓練 LightGBM 並跟 baseline 比較")
    ap.add_argument("--threshold", type=int, default=1, help="借不到車 = 可借 <= 這個數字（預設 1）")
    ap.add_argument("--end", default=yesterday_taipei(), help="只用到這一天為止（含），預設昨天")
    ap.add_argument("--test-days", type=int, default=4)
    ap.add_argument("--valid-days", type=int, default=2)
    ap.add_argument("--groups", nargs="+", choices=list(FEATURE_GROUPS),
                    help="只用這幾組特徵（預設全部）：" + "、".join(FEATURE_GROUPS))
    ap.add_argument("--name", help="輸出檔名的名稱（預設 le<門檻>）")
    ap.add_argument("--folds", action="store_true", help="3-8：在 FOLDS 的三段測試期各做一次")
    args = ap.parse_args()

    features = feature_columns(args.groups)
    name = args.name or "le{}".format(args.threshold)

    days = [d["date"] for d in available_days() if d["date"] <= args.end]
    test_days = days[-args.test_days:]
    valid_days = days[-(args.test_days + args.valid_days):-args.test_days]

    print("讀資料、算特徵…（約 30 秒）", flush=True)
    history = load_snapshots(end=args.end, district=MODEL_DISTRICTS, verbose=False)
    samples = make_labels(history, threshold=args.threshold, verbose=False)
    X = build_features(samples, history)
    print("{:,} 筆樣本、{} 個特徵（{}）".format(
        len(X), len(features), "、".join(args.groups or FEATURE_GROUPS)))
    print()
    print("=== 借不到車 = 30 分鐘後可借 <= {} ===".format(args.threshold))

    if args.folds:
        run_folds(X, features, name)
        return

    rows, model, test_scores = run_period(X, valid_days, test_days, features)
    print()
    table = print_table(rows)

    imp = feature_importance(model, features)
    print()
    print("特徵重要度（前 15 名，gain 佔比）：")
    print(imp.head(15).map("{:.1%}".format).to_string())

    os.makedirs(OUT_DIR, exist_ok=True)
    table.to_csv(os.path.join(OUT_DIR, "scorecard_{}.csv".format(name)), index=False)
    imp.rename("gain_share").to_csv(os.path.join(OUT_DIR, "importance_{}.csv".format(name)))
    test_scores.to_parquet(os.path.join(OUT_DIR, "test_pred_{}.parquet".format(name)), index=False)
    model.booster_.save_model(os.path.join(OUT_DIR, "lgbm_{}.txt".format(name)))
    print()
    print("結果存到 {}".format(OUT_DIR))


if __name__ == "__main__":
    main()
