"""
phase 2：跑兩個 baseline，印出成績單。

流程：讀資料 -> 建標籤（2-1）-> 照時間切（2-2）-> 兩個 baseline 打分數（2-4、2-5）
     -> 用 PR-AUC 與 recall@精確率 評分（2-3）

用法：
    python scripts/run_baselines.py                       # 預設：大安區、門檻 0 和 1 都跑
    python scripts/run_baselines.py --threshold 1         # 只跑「可借 <= 1」
    python scripts/run_baselines.py --test-days 2 --valid-days 1

切法預設是「最後 2 天測試、再往前 1 天驗證、其餘訓練」。
資料越多，這幾個數字就該跟著調大（例如累積 6 週後改成最後 1 週測試）。
"""

import argparse

import pandas as pd

from youbike.baselines import historical_average_score, persistence_score
from youbike.data import available_days, load_snapshots
from youbike.dataset import make_labels, time_split
from youbike.evaluate import precision_recall_at, report

# 跟 check_coverage.py 一樣的尖峰定義（台北時間，看的是「答案時刻」落在哪）
PEAKS = [(7, 10), (17, 20)]


def is_peak(df):
    h = df["target_hour"]
    return pd.concat([(h >= a) & (h < b) for a, b in PEAKS], axis=1).any(axis=1)


def evaluate_split(name, part, train, threshold, min_precision):
    """一段資料（驗證或測試）上，兩個 baseline 的成績。"""
    rows = []
    hist = historical_average_score(train, part, verbose=False)
    pers = persistence_score(part)
    for subset_name, mask in (("全天", slice(None)), ("尖峰", is_peak(part))):
        y = part["y"][mask]
        for model, score in (("① 維持現狀", pers[mask]), ("② 歷史同時段平均", hist[mask])):
            r = report(model, y, score, min_precision)
            r = {"資料": name, "時段": subset_name, **r}
            rows.append(r)
        # 「維持現狀」最直覺的用法：現在 <= 門檻，就猜 30 分鐘後也 <= 門檻
        p, rc = precision_recall_at(y, part["bikes_available"][mask] <= threshold)
        rows[-2]["直接照抄現在 P/R"] = "{:.2f} / {:.2f}".format(p, rc)
    return rows


def main():
    ap = argparse.ArgumentParser(description="跑兩個 baseline 並印出成績單")
    ap.add_argument("--district", default="大安區")
    ap.add_argument("--threshold", type=int, action="append",
                    help="借不到車 = 可借 <= 這個數字。可以給多次；預設 0 和 1 都跑")
    ap.add_argument("--test-days", type=int, default=2, help="最後幾天當測試（預設 2）")
    ap.add_argument("--valid-days", type=int, default=1, help="測試之前幾天當驗證（預設 1）")
    ap.add_argument("--min-precision", type=float, default=0.8)
    args = ap.parse_args()
    thresholds = args.threshold or [0, 1]

    days = [d["date"] for d in available_days()]
    n_eval = args.test_days + args.valid_days
    if len(days) <= n_eval:
        raise SystemExit("資料只有 {} 天，不夠切出 {} 天驗證 + {} 天測試".format(
            len(days), args.valid_days, args.test_days))
    test_days = days[-args.test_days:]
    valid_days = days[-n_eval:-args.test_days]

    df = load_snapshots(district=args.district, verbose=False)
    print("{}：{:,} 列、{} 站、{} ~ {}".format(
        args.district, len(df), df["station_id"].nunique(), days[0], days[-1]))
    print()

    all_rows = []
    for th in thresholds:
        print("=== 借不到車 = 30 分鐘後可借 <= {} ===".format(th))
        samples = make_labels(df, threshold=th)
        train, valid, test = time_split(samples, valid_days, test_days)
        for name, part in (("驗證", valid), ("測試", test)):
            for r in evaluate_split(name, part, train, th, args.min_precision):
                all_rows.append({"門檻": "<= {}".format(th), **r})
        print()

    table = pd.DataFrame(all_rows)
    for col in ("base rate", "PR-AUC", "recall@P{:.0f}".format(args.min_precision * 100)):
        table[col] = table[col].map("{:.3f}".format)
    table["直接照抄現在 P/R"] = table["直接照抄現在 P/R"].fillna("")
    print(table.to_markdown(index=False) if _has_tabulate() else table.to_string(index=False))


def _has_tabulate():
    # to_markdown 需要 tabulate 套件；沒裝就退回純文字表格，不為了排版多裝一個套件
    try:
        import tabulate  # noqa: F401
        return True
    except ImportError:
        return False


if __name__ == "__main__":
    main()
