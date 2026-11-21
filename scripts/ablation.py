"""
phase 3-9：特徵組的「拿掉一組」實驗（ablation）。

做法：每次拿掉一組特徵，在 3-8 的三段測試期各重新訓練一次，看分數掉多少。
為什麼不只看特徵重要度（gain）：重要度高不代表「少了它就不行」——
如果另一個特徵帶有差不多的資訊，拿掉後模型會改用那個，分數幾乎不變。
「拿掉之後掉多少」才是這組特徵真正不可取代的貢獻。

用法：
    python scripts/ablation.py            # 約 5 分鐘
結果存到 data/processed/model/ablation_le1.csv
"""

import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from train_model import FOLDS, OUT_DIR, run_period  # noqa: E402

from youbike.data import MODEL_DISTRICTS, load_snapshots  # noqa: E402
from youbike.dataset import make_labels  # noqa: E402
from youbike.features import FEATURE_GROUPS, build_features, feature_columns  # noqa: E402

END = "2026-11-02"   # 跟 3-8 用同一份資料，結果才能直接比


def main():
    history = load_snapshots(end=END, district=MODEL_DISTRICTS, verbose=False)
    X = build_features(make_labels(history, threshold=1, verbose=False), history)

    settings = {"全部特徵": list(FEATURE_GROUPS)}
    for g in FEATURE_GROUPS:
        settings["拿掉 " + g] = [x for x in FEATURE_GROUPS if x != g]
    settings["只有 station"] = ["station"]

    rows = []
    for name, groups in settings.items():
        for fold, (valid_days, test_days) in FOLDS.items():
            fold_rows, model, _ = run_period(X, valid_days, test_days, feature_columns(groups), verbose=False)
            for r in fold_rows:
                if r["資料"] == "測試" and r["模型"] == "③ LightGBM":
                    rows.append({"設定": name, "段": fold, "時段": r["時段"],
                                 "PR-AUC": r["PR-AUC"], "recall@P70": r["recall@P70"]})
            print("{}｜第 {} 段完成（{} 棵樹）".format(name, fold, model.best_iteration_), flush=True)

    table = pd.DataFrame(rows)
    os.makedirs(OUT_DIR, exist_ok=True)
    table.to_csv(os.path.join(OUT_DIR, "ablation_le1.csv"), index=False)
    summary = table.groupby(["設定", "時段"], sort=False)[["PR-AUC", "recall@P70"]].mean().unstack("時段")
    print(summary.round(3).to_string())


if __name__ == "__main__":
    main()
