"""
phase 4-5：預測程式 scripts/predict.py 從頭到尾跑一次的檢查。

用人造資料做一個跟 data 分支一樣結構的資料夾、訓練一個很小的模型，
實際執行 predict.py，確認：
    - 寫出 predictions/latest.json 和預測紀錄、站的計數表
    - 同一刻再跑一次會跳過（不會有重複紀錄）
    - 預測的機率 = 用離線特徵丟進同一個模型的機率（上線和離線一致）
"""

import json
import os
import subprocess
import sys

import numpy as np
import pandas as pd

from test_daily_low import write_data_dir
from test_features import fake_history, fake_stations, fake_weather
from youbike.citywide import citywide_totals
from youbike.dataset import make_labels
from youbike.features import build_features, feature_columns
from youbike.model import to_matrix, train

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def make_world(tmp_path):
    history = fake_history(days=9)
    # 天氣用真的大安區測站代號，predict.py 才讀得到
    weather = fake_weather(days=9).replace({"station_id": {"W1": "CAAH60", "W2": "A0A010"}})
    data_dir = os.path.join(str(tmp_path), "data")
    write_data_dir(data_dir, history, sorted(history["date"].unique()))
    os.makedirs(os.path.join(data_dir, "weather"), exist_ok=True)
    wday = weather["obs_time"].dt.tz_convert("Asia/Taipei").dt.strftime("%Y-%m-%d")
    for day, part in weather.groupby(wday):
        part.to_parquet(os.path.join(data_dir, "weather", day + ".parquet"), index=False)

    # 一個很小的模型，加上它的身分證
    X = build_features(make_labels(history, threshold=1, verbose=False), history,
                       stations=fake_stations(), citywide=citywide_totals(history), weather=weather)
    model = train(X, X, feature_columns(), params={"n_estimators": 20}, verbose=False)
    model_dir = os.path.join(str(tmp_path), "models")
    os.makedirs(model_dir)
    model.booster_.save_model(os.path.join(model_dir, "lgbm_le1.txt"))
    card = {"version": "test", "target": "30 分鐘後可借車輛 <= 1", "horizon_min": 30,
            "data": {"end": "2026-10-09"}, "model_file": "lgbm_le1.txt", "features": feature_columns(),
            "valid": {"pr_auc": 0.5, "recall_p70": 0.5},
            "alert": {"precision_target": 0.7, "score_threshold": 0.5}}
    with open(os.path.join(model_dir, "model_card.json"), "w", encoding="utf-8") as f:
        json.dump(card, f)
    return history, weather, data_dir, model_dir, model


def run_predict(data_dir, model_dir):
    return subprocess.run([sys.executable, os.path.join(REPO, "scripts", "predict.py"),
                           "--data-dir", data_dir, "--model-dir", model_dir],
                          capture_output=True, text=True, check=True).stdout


def test_predict_end_to_end(tmp_path):
    history, weather, data_dir, model_dir, model = make_world(tmp_path)
    out = run_predict(data_dir, model_dir)
    assert "預測完成" in out

    latest = json.load(open(os.path.join(data_dir, "predictions", "latest.json"), encoding="utf-8"))
    t = history["src_time"].max()
    assert pd.Timestamp(latest["data_time"]) == t
    assert len(latest["stations"]) == 3
    assert all(0 <= s["prob"] <= 1 for s in latest["stations"])
    meta = json.load(open(os.path.join(data_dir, "predictions", "stations.json"), encoding="utf-8"))
    assert {m["id"] for m in meta} == {s["id"] for s in latest["stations"]}
    assert os.path.isfile(os.path.join(data_dir, "profiles", "daily_low.parquet"))

    day = t.tz_convert("Asia/Taipei")
    log_path = os.path.join(data_dir, "predictions", day.strftime("%Y-%m-%d"),
                            day.strftime("%Y%m%dT%H%M%S") + ".parquet")
    log = pd.read_parquet(log_path)
    assert len(log) == 3 and set(log["model_version"]) == {"test"}

    # 上線的機率 = 離線特徵丟進同一個模型的機率
    # （最後一刻 30 分鐘後沒有資料，沒有答案，所以這裡直接用那一刻的列當樣本）
    samples = history[history["src_time"] == t][["station_id", "src_time", "date"]]
    X = build_features(samples, history, stations=fake_stations(), citywide=citywide_totals(history),
                       weather=weather[weather["fetched_at"] <= t])
    expect = pd.Series(model.booster_.predict(to_matrix(X, feature_columns())), index=X["station_id"].values)
    got = log.set_index("station_id")["prob"]
    assert np.allclose(got.loc[expect.index].to_numpy(), expect.to_numpy(), atol=1e-6)

    # 同一刻再跑一次：跳過，不會多一個紀錄檔
    out = run_predict(data_dir, model_dir)
    assert "已經預測過" in out
    assert len(os.listdir(os.path.dirname(log_path))) == 1


def test_predict_without_weather(tmp_path):
    # 天氣資料夾不見了（收集失敗）：照常預測，天氣特徵當成缺值
    _, _, data_dir, model_dir, _ = make_world(tmp_path)
    import shutil
    shutil.rmtree(os.path.join(data_dir, "weather"))
    assert "預測完成" in run_predict(data_dir, model_dir)


def test_feature_mismatch_stops(tmp_path):
    # 程式的特徵改了、模型沒重新訓練：寧可停下來，不要用錯的特徵預測
    _, _, data_dir, model_dir, _ = make_world(tmp_path)
    path = os.path.join(model_dir, "model_card.json")
    card = json.load(open(path, encoding="utf-8"))
    card["features"] = card["features"][:-1]
    json.dump(card, open(path, "w", encoding="utf-8"))
    r = subprocess.run([sys.executable, os.path.join(REPO, "scripts", "predict.py"),
                        "--data-dir", data_dir, "--model-dir", model_dir], capture_output=True, text=True)
    assert r.returncode != 0 and "特徵清單" in (r.stdout + r.stderr)
