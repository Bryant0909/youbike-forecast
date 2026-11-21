"""
phase 4-5：上線預測 —— 每 5 分鐘收集完資料之後跑一次。

做的事：
    1. 把「昨天」補進站的歷史計數表（profiles/daily_low.parquet；平常什麼都不用補）
    2. 讀最近幾天的資料，算出「最新一刻」每一站的特徵（youbike.online，跟訓練時同一套算法）
    3. 用 models/ 裡的模型預測每一站「30 分鐘後可借 <= 1」的機率
    4. 寫出檔案到資料資料夾（GitHub Actions 上就是 data 分支）：
         predictions/latest.json                     網頁讀的：每站的機率、現在車數、更新時間
         predictions/stations.json                   網頁讀的：站名、行政區、經緯度（內容有變才重寫）
         predictions/<日期>/<資料時間>.parquet         每次預測的紀錄（之後對答案算真實準確率）

    為什麼站名、經緯度另外放：latest.json 每 5 分鐘覆寫一次，data 分支的 git 會留下
    每一個版本。不會變的東西跟著重複存 288 次／天很浪費，網頁每次也要多下載。

同一個資料時刻已經預測過就跳過（收集那次沒有抓到新資料時會這樣），
所以重複執行也不會產生重複的紀錄。

用法：
    python scripts/predict.py                                  # 讀 data-branch/（或 YOUBIKE_DATA_DIR）
    python scripts/predict.py --data-dir "$GITHUB_WORKSPACE/data-branch"
"""

import argparse
import datetime
import json
import os
import time

import pandas as pd

from youbike.daily_low import update_daily_low
from youbike.data import REPO_ROOT, default_data_dir
from youbike.features import feature_columns
from youbike.model import to_matrix
from youbike.online import latest_features, load_online_inputs

MODEL_DIR = os.path.join(REPO_ROOT, "models")
TAIPEI = "Asia/Taipei"


def load_model(model_dir):
    """讀模型和它的身分證（model_card.json），並確認特徵清單跟現在的程式一致。"""
    import lightgbm as lgb

    with open(os.path.join(model_dir, "model_card.json"), encoding="utf-8") as f:
        card = json.load(f)
    if card["features"] != feature_columns():
        # 程式改了特徵、但模型還是舊的 -> 特徵對不上，預測會是錯的。寧可停下來。
        raise SystemExit("模型的特徵清單跟程式不一致：請重新跑 scripts/train_production.py")
    booster = lgb.Booster(model_file=os.path.join(model_dir, card["model_file"]))
    return booster, card


def write_json(path, obj):
    # 先寫暫存檔再改名：網頁不會讀到寫到一半的檔案
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)


def main():
    ap = argparse.ArgumentParser(description="用最新的資料預測每一站 30 分鐘後會不會借不到車")
    ap.add_argument("--data-dir", default=None, help="資料資料夾（預設 data-branch/ 或 YOUBIKE_DATA_DIR）")
    ap.add_argument("--model-dir", default=MODEL_DIR)
    args = ap.parse_args()
    data_dir = args.data_dir or default_data_dir()
    t0 = time.time()

    booster, card = load_model(args.model_dir)

    # 1. 站的歷史計數表：補上還沒算的日子
    daily_low, added = update_daily_low(data_dir, verbose=True)

    # 2. 最新一刻的特徵
    inputs = load_online_inputs(data_dir)
    X = latest_features(inputs["history"], inputs["stations"], inputs["citywide"],
                        inputs["weather"], daily_low)
    t = X["src_time"].iloc[0]
    t_local = t.tz_convert(TAIPEI)

    log_dir = os.path.join(data_dir, "predictions", t_local.strftime("%Y-%m-%d"))
    log_path = os.path.join(log_dir, t_local.strftime("%Y%m%dT%H%M%S") + ".parquet")
    if os.path.exists(log_path):
        print("{} 這一刻已經預測過了，跳過".format(t_local.strftime("%Y-%m-%d %H:%M:%S")))
        return

    # 3. 預測
    prob = booster.predict(to_matrix(X, card["features"]))
    thr = card["alert"]["score_threshold"]
    X["prob"] = prob.astype("float32")
    X["alert"] = X["prob"] >= thr
    now = datetime.datetime.now(datetime.timezone.utc)

    # 4a. 預測紀錄（phase 5 對答案用）。欄位要跟 compact.py 的 predictions 規格一致
    log = pd.DataFrame({
        "station_id": X["station_id"].astype(str),
        "src_time": X["src_time"],
        "bikes_available": X["bikes_available"].astype("int16"),
        "prob": X["prob"],
        "alert": X["alert"],
        "model_version": card["version"],
        "predicted_at": pd.Timestamp(now).tz_convert("UTC").as_unit("ms"),
    })
    os.makedirs(log_dir, exist_ok=True)
    log.to_parquet(log_path, index=False)

    # 4b. 網頁讀的站點資料（站名、位置）：內容有變才重寫
    st = inputs["stations"].set_index("station_id")
    meta = [{
        "id": sid,
        "name": str(st.loc[sid, "name"]).replace("YouBike2.0_", ""),
        "district": st.loc[sid, "district"],
        "lat": round(float(st.loc[sid, "lat"]), 5),
        "lon": round(float(st.loc[sid, "lon"]), 5),
    } for sid in sorted(X["station_id"])]
    meta_path = os.path.join(data_dir, "predictions", "stations.json")
    old = None
    if os.path.exists(meta_path):
        with open(meta_path, encoding="utf-8") as f:
            old = json.load(f)
    if old != meta:
        write_json(meta_path, meta)

    # 4c. 網頁讀的最新預測：只放會變的數字
    rows = [{
        "id": r.station_id,
        "bikes": int(r.bikes_available),
        "docks": int(r.docks_available),
        "bikes_30m_ago": None if pd.isna(r.bikes_lag30) else int(r.bikes_lag30),
        "prob": round(float(r.prob), 3),
        "alert": bool(r.alert),
    } for r in X.sort_values("prob", ascending=False).itertuples()]
    write_json(os.path.join(data_dir, "predictions", "latest.json"), {
        "data_time": t_local.isoformat(),
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "horizon_min": card["horizon_min"],
        "target": card["target"],
        "model": {
            "version": card["version"],
            "trained_through": card["data"]["end"],
            "valid_pr_auc": round(card["valid"]["pr_auc"], 3),
            "valid_recall_p70": round(card["valid"]["recall_p70"], 3),
            "valid_days": card["valid"].get("days"),
            "alert_threshold": round(thr, 3),
            "alert_precision": card["alert"]["precision_target"],
        },
        "stations": rows,
    })

    n_alert = int(X["alert"].sum())
    print("預測完成：資料時間 {}、{} 站、{} 站有警告；計數表補了 {} 天；花了 {:.1f} 秒".format(
        t_local.strftime("%Y-%m-%d %H:%M:%S"), len(X), n_alert, len(added), time.time() - t0))


if __name__ == "__main__":
    main()
