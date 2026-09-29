from datetime import date, timedelta

import pytest

from app.trend import build_trend


def closes(start: str, days: int, price):
    """每天一筆收盤價。price 可以是固定數字，也可以是吃「第幾天」的函式。"""
    d0 = date.fromisoformat(start)
    return [{"date": (d0 + timedelta(days=i)).isoformat(),
             "close": price(i) if callable(price) else price}
            for i in range(days)]


def known(dates, eps=10.0):
    return [(d, eps) for d in dates]


def quarterly(first: str, n: int) -> list[str]:
    d = date.fromisoformat(first)
    return [(d + timedelta(days=91 * i)).isoformat() for i in range(n)]


def test_a_flat_stock_has_a_flat_channel_centred_on_its_price():
    rel = quarterly("2024-01-10", 9)
    t = build_trend({"releases": rel, "closes": closes("2024-01-01", 780, 100.0)}, known(rel), 2)
    assert t["now"]["fair"] == pytest.approx(100.0, abs=0.01)
    assert t["now"]["sigma"] == pytest.approx(0.0, abs=0.01)
    assert t["now"]["verdict"]["code"] == "fair"


def test_the_channel_follows_a_rising_stock_instead_of_calling_it_expensive():
    # 穩定上漲的股票不該每天都被判定「太貴」—— 軌道要跟著爬
    rel = quarterly("2024-01-10", 9)
    t = build_trend({"releases": rel, "closes": closes("2024-01-01", 780, lambda i: 100.0 + i * 0.1)}, known(rel), 2)
    assert t["now"]["fair"] > 150
    assert t["now"]["verdict"]["code"] == "fair"


def test_a_sudden_crash_falls_out_of_the_channel():
    rel = quarterly("2024-01-10", 9)
    price = lambda i: 100.0 if i < 777 else 40.0   # 最後三天才崩，軌道還來不及跟下去
    t = build_trend({"releases": rel, "closes": closes("2024-01-01", 780, price)}, known(rel), 2)
    assert t["now"]["verdict"]["code"] == "cheap"
    assert t["now"]["gap"] < -20


def test_the_slope_is_dollars_per_day_not_per_data_point():
    # 斜率若用「第幾筆」當 x，軌道會被資料密度扭曲；這裡少掉一半的日子，結果必須一樣
    rel = quarterly("2024-01-10", 9)
    dense = closes("2024-01-01", 780, lambda i: 100.0 + i * 0.1)
    sparse = [c for i, c in enumerate(dense) if i % 2 == 0]
    a = build_trend({"releases": rel, "closes": dense}, known(rel), 2)["now"]["fair"]
    b = build_trend({"releases": rel, "closes": sparse}, known(rel), 2)["now"]["fair"]
    assert a == pytest.approx(b, abs=1.0)


def test_the_last_channel_runs_to_the_next_earnings_date_not_to_today():
    # 這張圖要回答「接下來這一季的合理區間」，停在今天就沒有區間可以對照
    rel = quarterly("2024-01-10", 10)        # 第 10 筆是還沒發生的下一次公布日
    c = closes("2024-01-01", 780, 100.0)
    t = build_trend({"releases": rel, "closes": c}, known(rel[:9]), 2)
    assert t["today"] == c[-1]["date"]
    assert t["segments"][-1]["to"] == rel[9] > t["today"]


def test_with_no_scheduled_date_the_projection_is_one_quarter_long():
    rel = quarterly("2024-01-10", 9)
    t = build_trend({"releases": rel, "closes": closes("2024-01-01", 780, 100.0)}, known(rel), 2)
    s = t["segments"][-1]
    assert (date.fromisoformat(s["to"]) - date.fromisoformat(s["at"])).days == 106


def test_projecting_forward_does_not_produce_an_absurd_price():
    # 舊做法拿四週斜率推四個月，TSLA 推出過一季跌到剩五分之一。橫盤股推出去必須還是橫的
    rel = quarterly("2024-01-10", 10)
    t = build_trend({"releases": rel, "closes": closes("2024-01-01", 740, 100.0)}, known(rel[:9]), 2)
    assert t["segments"][-1]["y1"] == pytest.approx(100.0, abs=2.0)


def test_a_just_published_quarter_borrows_days_instead_of_wild_extrapolation():
    # 財報剛公布，這一季只有 5 天資料卻要畫 91 天。這 5 天剛好是暴漲，不准把它推成整季
    rel = quarterly("2024-01-10", 10)
    spike = lambda i: 100.0 if i < 735 else 100.0 + (i - 734) * 12.0
    t = build_trend({"releases": rel, "closes": closes("2024-01-01", 740, spike)}, known(rel[:9]), 2)
    assert t["segments"][-1]["y1"] < 200.0     # 原樣外推會衝到 700 以上


def test_a_full_quarter_of_data_is_not_polluted_by_the_previous_quarter():
    # 這一季的資料夠長，斜率就只能看這一季。往前借會把上一季的走勢混進來
    rel = quarterly("2024-01-10", 10)
    fell = lambda i: 200.0 - i * 0.2 if i < 690 else 62.0 + (i - 690) * 1.0
    t = build_trend({"releases": rel, "closes": closes("2024-01-01", 780, fell)}, known(rel[:9]), 2)
    assert t["segments"][-1]["y1"] > t["segments"][-1]["y0"]   # 這一季在漲，不准被上一季拖成跌


def test_the_window_only_covers_the_years_asked_for():
    rel = quarterly("2022-01-10", 17)
    t5 = build_trend({"releases": rel, "closes": closes("2022-01-01", 1500, 100.0)}, known(rel), 5)
    t1 = build_trend({"releases": rel, "closes": closes("2022-01-01", 1500, 100.0)}, known(rel), 1)
    assert len(t5["segments"]) > len(t1["segments"])
    assert t1["points"][0]["d"] > t5["points"][0]["d"]


def test_the_dots_are_every_trading_day_and_do_not_move_when_the_slider_moves():
    # 股價是事實，不該隨著回看年數改變。抽樣畫縮圖的話，換年數就換一批被抽中的日子
    rel = quarterly("2022-01-10", 17)
    c = closes("2022-01-01", 1500, lambda i: 100.0 + i * 0.05)
    fin = {"releases": rel, "closes": c}
    wide = {p["d"]: p["c"] for p in build_trend(fin, known(rel), 5)["points"]}
    narrow = {p["d"]: p["c"] for p in build_trend(fin, known(rel), 2)["points"]}
    assert len(wide) == len(c)                       # 一個交易日一個點，沒有抽樣
    assert narrow.items() <= wide.items()            # 短區間必須是長區間的子集，價格一模一樣


def test_no_earnings_in_the_window_raises_instead_of_drawing_a_fake_channel():
    with pytest.raises(RuntimeError):
        old = quarterly("2019-01-10", 9)
        build_trend({"releases": old, "closes": closes("2024-01-01", 400, 100.0)}, known(old), 1)


def test_too_few_prices_raises():
    with pytest.raises(RuntimeError):
        rel = quarterly("2024-01-10", 9)
        build_trend({"releases": rel, "closes": closes("2024-01-01", 1, 100.0)}, known(rel), 1)
