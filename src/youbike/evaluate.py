"""
phase 2-3：評估指標 —— 刻意用 numpy 自己寫，不靠 scikit-learn。

┌─ 為什麼不看 accuracy（準確率）─────────────────────────────────────┐
│ 「借不到車」只佔所有時刻的一小部分（大安區約 8~18%，看門檻）。      │
│ 一個永遠猜「借得到」的笨模型，accuracy 就有 82~92% —— 看起來很高，  │
│ 但它一次都沒有幫到人。這種「少數類別才是重點」的問題要看：          │
│                                                                    │
│ precision（精確率）：模型說「會借不到」的時候，有多少比例真的借不到 │
│ recall（召回率）   ：真的借不到的那些時刻，模型抓到了多少比例       │
└────────────────────────────────────────────────────────────────────┘

┌─ PR-AUC（這裡用 average precision 算）──────────────────────────────┐
│ 模型輸出的是一個「風險分數」，分數越高越可能借不到。                │
│ 把門檻從高往低移，每個門檻都有一組 (recall, precision)，             │
│ 畫出來就是 PR 曲線；曲線下的面積就是 PR-AUC。                        │
│   - 1.0 = 完美                                                       │
│   - 「亂猜」的分數 ≈ 正樣本比例（base rate）—— 不是 0.5！            │
│ 所以報告一定要把 base rate 一起印出來，才知道分數有沒有意義。        │
└────────────────────────────────────────────────────────────────────┘

為什麼自己寫：scikit-learn 原本就規劃到 phase 3 才裝（而且這台電腦的
「智慧型應用程式控制」曾經擋過 pandas 3 的 DLL，多一個套件就多一個風險）。
自己寫一次也比較能理解這些數字到底是什麼。演算法跟 sklearn 的
average_precision_score 相同（同分的樣本視為同一個門檻）。
"""

import numpy as np


def _pr_points(y_true, score):
    """
    回傳每個「不同分數門檻」下的 (precision, recall)，門檻由高到低。

    【同分的處理很重要】
    「維持現狀」這種 baseline 的分數只有幾種值（例如 -0, -1, -2 台車），
    同分的樣本一定要一起算進去 —— 不能因為排序時誰先誰後，就讓
    同一個分數的樣本有的算「預測會借不到」、有的不算。
    """
    y = np.asarray(y_true, dtype=bool)
    s = np.asarray(score, dtype=float)
    order = np.argsort(-s, kind="mergesort")      # 分數由高到低
    y, s = y[order], s[order]

    tp = np.cumsum(y)                             # 到第 i 筆為止抓到幾個真的
    fp = np.cumsum(~y)
    # 只在「分數改變之前的最後一筆」取值 = 每個不同的門檻取一個點
    last_of_tie = np.r_[s[1:] != s[:-1], True]
    tp, fp = tp[last_of_tie], fp[last_of_tie]

    precision = tp / (tp + fp)
    recall = tp / y.sum() if y.sum() else np.zeros_like(tp, dtype=float)
    return precision, recall


def average_precision(y_true, score):
    """PR-AUC（average precision）：每次 recall 增加的量 × 當時的 precision，加總。"""
    precision, recall = _pr_points(y_true, score)
    recall_gain = np.diff(np.r_[0.0, recall])
    return float(np.sum(recall_gain * precision))


def recall_at_precision(y_true, score, min_precision=0.8):
    """
    「精確率至少 min_precision 的前提下，最多能抓到多少」。

    這比 PR-AUC 更貼近實際使用：如果 App 說「這站 30 分鐘後會沒車」，
    十次裡至少要對八次使用者才會信它。在這個前提下能提前警告幾成的狀況，
    就是這個數字。完全達不到這個精確率就回傳 0。
    """
    precision, recall = _pr_points(y_true, score)
    ok = precision >= min_precision
    return float(recall[ok].max()) if ok.any() else 0.0


def precision_recall_at(y_true, y_pred):
    """某一個固定門檻（已經是 True/False 的預測）的 precision 與 recall。"""
    y = np.asarray(y_true, dtype=bool)
    p = np.asarray(y_pred, dtype=bool)
    tp = int((y & p).sum())
    precision = tp / p.sum() if p.sum() else 0.0
    recall = tp / y.sum() if y.sum() else 0.0
    return precision, recall


def report(name, y_true, score, min_precision=0.8):
    """一個模型在一份資料上的成績單，回傳 dict（方便組成表格）。"""
    y = np.asarray(y_true, dtype=bool)
    return {
        "模型": name,
        "樣本數": len(y),
        "base rate": float(y.mean()),
        "PR-AUC": average_precision(y, score),
        "recall@P{:.0f}".format(min_precision * 100): recall_at_precision(y, score, min_precision),
    }
