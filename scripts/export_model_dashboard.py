"""
phase 3-10：把模型的成果匯出成網頁（「YouBike 模型成果」Dashboard）用的 JSON。

網頁上的每個數字都要能追到一份資料表，所以這裡把 notebooks/03_model.ipynb
的結果整理成幾份小表。先跑過這三個，這支才有東西可以讀：

    python scripts/train_model.py --end 2026-11-02            # C 段的預測（test_pred_le1.parquet）、特徵重要度
    python scripts/train_model.py --end 2026-11-02 --folds    # 三段測試期（folds_le1.csv）
    python scripts/ablation.py                                # 特徵組實驗（ablation_le1.csv）

用法：
    python scripts/export_model_dashboard.py      # 輸出到 data/processed/model_dashboard/

輸出（每份都是 JSON 陣列）：
    folds          三段測試期 × 4 個子集合 × 3 個模型的成績
    pr_curve       C 段測試期三個模型的 PR 曲線（每個模型約 200 個點）
    ablation       拿掉一組特徵後的成績（三段平均）
    importance     特徵重要度（C 段模型）
    by_hour        各小時（答案時刻）的 PR-AUC
    calibration    模型說的機率 vs 實際比例
    misses         錯誤分析的幾個關鍵數字
    drain_stations 「現在 >= 5 台卻在 30 分鐘內被借光」漏報最多的站
"""

import json
import os

import numpy as np
import pandas as pd

from youbike.data import REPO_ROOT, load_stations
from youbike.evaluate import _pr_points, average_precision
from youbike.features import FEATURE_GROUPS

MODEL_DIR = os.path.join(REPO_ROOT, "data", "processed", "model")
OUT_DIR = os.path.join(REPO_ROOT, "data", "processed", "model_dashboard")

MODELS = {"score_persist": "① 維持現狀", "score_hist": "② 歷史同時段平均", "score_lgbm": "③ LightGBM"}

# 特徵的中文說明（網頁上給人看的名字）
FEATURE_ZH = {
    "bikes_available": "現在幾台", "docks_available": "現在幾個空位", "total_docks": "總車柱數",
    "fill_ratio": "車輛 / 車柱", "min_since_low": "距離上次 <= 1 台幾分鐘",
    "min_60m": "最近 60 分鐘最少幾台", "max_60m": "最近 60 分鐘最多幾台",
    "bikes_1d_at_target": "昨天同一時刻幾台", "bikes_7d_at_target": "上週同一時刻幾台",
    "target_tod_min": "幾點幾分", "target_weekday": "星期幾", "target_is_offday": "放不放假",
    "next_day_offday": "隔天放不放假", "prev_day_offday": "前一天放不放假",
    "is_campus": "是不是校區", "lat": "緯度", "lon": "經度",
    "low_rate_all": "過去這站 <= 1 台的比例", "low_rate_slot": "過去這站在同時段 <= 1 台的比例",
    "slot_days": "同時段比例用了幾天",
    "city_bikes": "全台北可借車輛", "city_ratio": "全台北車輛 / 車柱", "city_low_frac": "全台北快沒車的站比例",
    "city_bikes_diff60": "全台北跟 60 分鐘前比",
    "rain_30m": "最近 30 分鐘雨量", "rain_60m": "最近 60 分鐘雨量", "temp_c": "氣溫",
}
for m in (5, 15, 30, 60):
    FEATURE_ZH["bikes_lag{}".format(m)] = "{} 分鐘前幾台".format(m)
    FEATURE_ZH["diff{}".format(m)] = "跟 {} 分鐘前比".format(m)


def save(name, rows):
    def clean(v):
        if isinstance(v, (np.floating, float)):
            return None if np.isnan(v) else round(float(v), 4)
        if isinstance(v, np.integer):
            return int(v)
        if isinstance(v, np.bool_):
            return bool(v)
        return v
    rows = [{k: clean(v) for k, v in r.items()} for r in rows]
    with open(os.path.join(OUT_DIR, name + ".json"), "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=0)
    print("{:<15} {} 列".format(name, len(rows)))


def main():
    os.makedirs(OUT_DIR, exist_ok=True)

    # --- 三段測試期 ---
    folds = pd.read_csv(os.path.join(MODEL_DIR, "folds_le1.csv"))
    save("folds", [{
        "fold": r["段"], "subset": r["時段"], "model": r["模型"], "n": r["樣本數"],
        "base_rate": r["base rate"], "pr_auc": r["PR-AUC"],
        "recall_p60": r["recall@P60"], "recall_p70": r["recall@P70"], "recall_p80": r["recall@P80"],
    } for _, r in folds.iterrows()])

    # --- C 段的預測 ---
    d = pd.read_parquet(os.path.join(MODEL_DIR, "test_pred_le1.parquet"))

    rows = []
    grid = np.linspace(0, 1, 201)
    for col, name in MODELS.items():
        p, r = _pr_points(d["y"], d[col])
        if len(r) > len(grid):
            # 點太多：每個召回率格點取第一個到達它的點（曲線形狀不變，檔案小很多）
            idx = np.unique(np.searchsorted(r, grid).clip(max=len(r) - 1))
            p, r = p[idx], r[idx]
        rows += [{"model": name, "recall": rr, "precision": pp} for rr, pp in zip(r, p)]
    save("pr_curve", rows)

    rows = []
    for hour, g in d.groupby("target_hour"):
        for col, name in MODELS.items():
            rows.append({"hour": int(hour), "model": name, "pr_auc": average_precision(g["y"], g[col]),
                         "base_rate": g["y"].mean()})
    save("by_hour", rows)

    bins = pd.cut(d["score_lgbm"], np.linspace(0, 1, 11), include_lowest=True)
    cal = d.groupby(bins, observed=True).agg(n=("y", "size"), predicted=("score_lgbm", "mean"), actual=("y", "mean"))
    save("calibration", [{"bin": "{:.0%}–{:.0%}".format(b.left if b.left > 0 else 0, b.right), "n": r["n"],
                          "predicted": r["predicted"], "actual": r["actual"]} for b, r in cal.iterrows()])

    # --- 錯誤分析：精確率 70% 的警告門檻下，猜錯的是哪些 ---
    order = np.argsort(-d["score_lgbm"].to_numpy(), kind="mergesort")
    ys, ss = d["y"].to_numpy()[order], d["score_lgbm"].to_numpy()[order]
    prec = np.cumsum(ys) / np.arange(1, len(ys) + 1)
    thr = ss[np.where(prec >= 0.7)[0].max()]
    alert = d["score_lgbm"] >= thr
    fp, fn = d[alert & ~d["y"]], d[~alert & d["y"]]
    now_ok = d[d["bikes_available"] >= 2]
    big = fn[fn["bikes_available"] >= 5]
    misses = [
        ("sudden_share", "借不到的情況裡，「現在還有 >= 2 台」的比例", now_ok["y"].sum() / d["y"].sum()),
        ("sudden_ap_lgbm", "這些情況的 PR-AUC：LightGBM", average_precision(now_ok["y"], now_ok["score_lgbm"])),
        ("sudden_ap_persist", "這些情況的 PR-AUC：維持現狀", average_precision(now_ok["y"], now_ok["score_persist"])),
        ("alert_threshold", "精確率 70% 的警告門檻（風險分數）", thr),
        ("alert_share", "會發出警告的時刻比例", alert.mean()),
        ("fp_near_share", "誤報裡 30 分鐘後剛好 2 台的比例", (fp["bikes_future"] == 2).mean()),
        ("fn_big_share", "漏報裡現在還有 >= 5 台的比例", (fn["bikes_available"] >= 5).mean()),
        ("drain_avg_drop", "這些漏報 30 分鐘內平均少了幾台", (big["bikes_available"] - big["bikes_future"]).mean()),
    ]
    save("misses", [{"id": i, "label": label, "value": v} for i, label, v in misses])

    names = load_stations().set_index("station_id")["name"].str.replace("YouBike2.0_", "", regex=False)
    top = names.loc[big["station_id"]].value_counts().head(8)
    save("drain_stations", [{"station": s, "count": int(c)} for s, c in top.items()])

    # --- 特徵 ---
    imp = pd.read_csv(os.path.join(MODEL_DIR, "importance_le1.csv"), index_col=0)["gain_share"]
    group_of = {c: g for g, cols in FEATURE_GROUPS.items() for c in cols}
    save("importance", [{"feature": f, "label": FEATURE_ZH.get(f, f), "group": group_of[f], "gain_share": v}
                        for f, v in imp.head(15).items()])

    abl = pd.read_csv(os.path.join(MODEL_DIR, "ablation_le1.csv"))
    avg = abl.groupby(["設定", "時段"], sort=False)[["PR-AUC", "recall@P70"]].mean().reset_index()
    full = avg[avg["設定"] == "全部特徵"].set_index("時段")
    save("ablation", [{
        "setting": r["設定"], "subset": r["時段"], "pr_auc": r["PR-AUC"], "recall_p70": r["recall@P70"],
        "delta_pr_auc": r["PR-AUC"] - full.loc[r["時段"], "PR-AUC"],
        "delta_recall_p70": r["recall@P70"] - full.loc[r["時段"], "recall@P70"],
    } for _, r in avg.iterrows()])

    print("輸出到", OUT_DIR)


if __name__ == "__main__":
    main()
