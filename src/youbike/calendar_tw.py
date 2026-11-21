"""
phase 3-3：台灣的放假行事曆 + 時間特徵。

┌─ 為什麼需要行事曆，只看「星期幾」不夠嗎 ───────────────────────────┐
│ EDA 發現 10/9（週五，國慶補假）的借車節奏跟週末一模一樣（phase 1-3）。│
│ 只看星期幾的模型會以為那天是平日，用上班日的尖峰去猜 —— 整天都猜錯。 │
│ 所以模型要知道的是「那天要不要上班」，而不只是「星期幾」。           │
└────────────────────────────────────────────────────────────────────┘

資料來源：行政院人事行政總處「115 年、116 年政府行政機關辦公日曆表」，
透過 https://github.com/ruyut/TaiwanCalendar（把官方日曆整理成 JSON）取得，
並與新聞報導（光復節 10/24~26、行憲紀念日 12/25~27 連假）以及
我們自己的資料（10/9 補假那天像週末）對照過。
2025 年修法後新增教師節、光復節、行憲紀念日三天，而且不再為了連假調整上班。

只列「本來該上班、但放假」的日子（週末本來就放假，不用列）。
如果有「週末但要補班」的日子，放進 MAKEUP_WORKDAYS（2026 年沒有）。
2027 年在 2026-11-22 加入（上線預測時，12/31 的「隔天放不放假」就會查到 2027 年）。
資料跨到 2028 年前，要把 2028 年的日曆加進來 —— 超出範圍會直接報錯提醒。
"""

import pandas as pd

WEEKDAY_HOLIDAYS = {
    "2026-01-01": "開國紀念日",
    "2026-02-16": "農曆除夕",
    "2026-02-17": "春節",
    "2026-02-18": "春節",
    "2026-02-19": "春節",
    "2026-02-20": "補假",
    "2026-02-27": "補假（和平紀念日）",
    "2026-04-03": "補假（兒童節）",
    "2026-04-06": "補假（清明節）",
    "2026-05-01": "勞動節",
    "2026-06-19": "端午節",
    "2026-09-25": "中秋節",
    "2026-09-28": "教師節",
    "2026-10-09": "補假（國慶日）",
    "2026-10-26": "補假（光復節）",
    "2026-12-25": "行憲紀念日",
    # 2027 年（116 年）
    "2027-01-01": "開國紀念日",
    "2027-02-04": "小年夜",
    "2027-02-05": "農曆除夕",
    "2027-02-08": "春節",
    "2027-02-09": "補假（春節）",
    "2027-02-10": "補假（春節）",
    "2027-03-01": "補假（和平紀念日）",
    "2027-04-05": "清明節",
    "2027-04-06": "補假（兒童節）",
    "2027-04-30": "補假（勞動節）",
    "2027-06-09": "端午節",
    "2027-09-15": "中秋節",
    "2027-09-28": "教師節",
    "2027-10-11": "補假（國慶日）",
    "2027-10-25": "光復節",
    "2027-12-24": "補假（行憲紀念日）",
    "2027-12-31": "補假（2028 開國紀念日）",
}

MAKEUP_WORKDAYS = {}   # 週末但要上班的日子（2026、2027 年都沒有）

COVERED_YEARS = {2026, 2027}   # 上面的表涵蓋哪些年


def is_offday(date):
    """這天放不放假（週末或國定假日，扣掉補班日）。date 是 'YYYY-MM-DD' 或 Timestamp。"""
    ts = pd.Timestamp(date)
    if ts.year not in COVERED_YEARS:
        raise ValueError("行事曆只涵蓋 {} 年，{} 超出範圍 —— 請把那一年的放假日加進 calendar_tw.py".format(
            sorted(COVERED_YEARS), ts.date()))
    key = ts.strftime("%Y-%m-%d")
    if key in MAKEUP_WORKDAYS:
        return False
    return ts.weekday() >= 5 or key in WEEKDAY_HOLIDAYS


def day_type(date):
    """'上班日' 或 '休息日'。"""
    return "休息日" if is_offday(date) else "上班日"


# ---------------------------------------------------------------------------
# 時間特徵
# ---------------------------------------------------------------------------

CALENDAR_FEATURES = ["target_tod_min", "target_weekday", "target_is_offday",
                     "next_day_offday", "prev_day_offday"]


def calendar_features(target_time):
    """
    「答案那個時刻」（t + 30 分鐘）的時間特徵。

    看答案時刻而不是現在，跟 baseline「歷史同時段平均」一樣：要猜的是 30 分鐘後。
    日曆是事先就知道的，所以用未來的日期不算偷看。

        target_tod_min    一天中的第幾分鐘（0~1439）。用分鐘而不是小時，
                          8:00 和 8:55 的狀況差很多，樹模型自己會找切點
        target_weekday    星期幾（0 = 週一）
        target_is_offday  那天放不放假（週末或國定假日）
        next_day_offday   隔天放不放假：週五晚上、連假前一晚的人流跟一般平日晚上不同
        prev_day_offday   前一天放不放假：收假第一天早上的人流可能不同

    target_time：台北時間或 UTC 的 Timestamp Series 都可以。
    回傳 DataFrame（index 跟輸入一樣）。
    """
    local = pd.to_datetime(target_time).dt.tz_convert("Asia/Taipei")
    dates = local.dt.normalize().dt.tz_localize(None)

    # 一天只算一次（200 萬列只有幾十個不同的日子）
    uniq = pd.Series(dates.unique())
    off = {d: is_offday(d) for d in uniq}
    off_next = {d: is_offday(d + pd.Timedelta(days=1)) for d in uniq}
    off_prev = {d: is_offday(d - pd.Timedelta(days=1)) for d in uniq}

    return pd.DataFrame({
        "target_tod_min": (local.dt.hour * 60 + local.dt.minute).astype("int16"),
        "target_weekday": local.dt.weekday.astype("int8"),
        "target_is_offday": dates.map(off).astype(bool),
        "next_day_offday": dates.map(off_next).astype(bool),
        "prev_day_offday": dates.map(off_prev).astype(bool),
    }, index=target_time.index)
