"""phase 3-3 行事曆的檢查。"""

import pandas as pd
import pytest

from youbike.calendar_tw import calendar_features, day_type, is_offday


def test_known_days():
    assert is_offday("2026-10-09")          # 週五，國慶補假（我們的資料也看到那天像週末）
    assert is_offday("2026-10-10")          # 週六
    assert not is_offday("2026-10-08")      # 普通週四
    assert is_offday("2026-10-26")          # 週一，光復節補假
    assert day_type("2026-11-02") == "上班日"
    assert day_type("2026-11-01") == "休息日"


def test_out_of_range_year_raises():
    with pytest.raises(ValueError):
        is_offday("2028-01-04")


def test_year_end_2026_to_2027():
    # 上線預測在 12/31 會查「隔天」：2027 年也要查得到
    assert is_offday("2027-01-01")          # 週五，開國紀念日
    assert is_offday("2027-02-04")          # 週四，小年夜
    assert not is_offday("2027-01-04")      # 普通週一
    assert is_offday("2027-12-31")          # 週五，2028 元旦（週六）的補假


def test_calendar_features():
    t = pd.Series(pd.to_datetime([
        "2026-10-08 22:10",   # 週四晚上，隔天是補假
        "2026-10-12 07:55",   # 週一早上，前一天是週日
        "2026-10-14 23:59",
    ]).tz_localize("Asia/Taipei").tz_convert("UTC"))
    f = calendar_features(t)
    assert f["target_tod_min"].tolist() == [22 * 60 + 10, 7 * 60 + 55, 23 * 60 + 59]
    assert f["target_weekday"].tolist() == [3, 0, 2]
    assert f["target_is_offday"].tolist() == [False, False, False]
    assert f["next_day_offday"].tolist() == [True, False, False]
    assert f["prev_day_offday"].tolist() == [False, True, False]
