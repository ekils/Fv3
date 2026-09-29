from datetime import date, timedelta

import pytest

from app.valuation import build_valuation


def fin(quarters, closes, revenue=None, gross=None, net=None, releases=None):
    return {"quarters": [{"period": p, "eps": v} for p, v in quarters],
            "closes": [{"date": d, "close": c} for d, c in closes],
            "releases": releases if releases is not None else announced(quarters),
            "revenue": revenue or {}, "gross": gross or {}, "net": net or {}}


def announced(quarters, lag=45):
    """每季結束後 lag 天公布。真實資料的落差是 15～53 天，這裡取中間值當預設。"""
    return sorted((date.fromisoformat(p) + timedelta(days=lag)).isoformat() for p, _ in quarters)


def quads(end_year, per_quarter, start_year=2024):
    """每季都賺 per_quarter，四季相加 = 一年 4×per_quarter。"""
    return [(f"{y}-{m:02d}-01", per_quarter)
            for y in range(start_year, end_year + 1) for m in (3, 6, 9, 12)]


def test_eps_is_only_used_after_the_report_lag():
    # 第一個滾動年度要等 2024-12-01 那季結束再過 45 天才公布，之前的日子算不出本益比
    f = fin(quads(2025, 2.5), [("2024-12-05", 100.0), ("2026-05-01", 200.0), ("2026-06-01", 200.0)])
    d = build_valuation(f, 5)
    assert d["pe"]["now"] == 20.0
    assert d["pe"]["low"] == d["pe"]["high"] == 20.0
    assert d["from"] == "2026-05-01"


def test_the_real_release_date_is_used_not_a_fixed_lag():
    # 同一批財報，公布得早市場就早知道，本益比的換檔時機要跟著提前
    q = quads(2024, 2.5)                      # 只有一個滾動年度，換檔時機才看得清楚
    closes = [("2024-12-20", 200.0), ("2025-03-01", 200.0), ("2025-04-01", 200.0)]
    late = build_valuation(fin(q, closes, releases=announced(q, 45)), 5)
    early = build_valuation(fin(q, closes, releases=announced(q, 15)), 5)
    assert late["from"] == "2025-03-01"        # 2025-01-15 才公布，12-20 那天還算不出來
    assert early["from"] == "2024-12-20"       # 2024-12-16 就公布了，12-20 已經知道


def test_a_quarter_with_no_release_date_yet_is_dropped():
    # 季末到了但財報還沒公布，不能假裝市場已經知道這個數字
    q = quads(2025, 2.5)
    f = fin(q, [("2026-06-01", 200.0)] * 2, releases=announced(q[:-1], 45))
    assert build_valuation(f, 5)["pe"]["now"] == 20.0


def test_looking_back_to_a_past_day_cannot_see_later_prices():
    # 框選到 2026-06-01 就該像站在那天：之後那根 400 元的高點不准算進區間
    from app.main import _as_of
    f = fin(quads(2025, 2.5, start_year=2019),
            [("2026-05-01", 100.0), ("2026-06-01", 200.0), ("2026-09-01", 400.0)])
    now, past = build_valuation(f, 5), build_valuation(_as_of(f, "2026-06-01"), 5)
    assert now["pe"]["high"] == 40.0 and now["to"] == "2026-09-01"
    assert past["pe"]["high"] == 20.0 and past["to"] == "2026-06-01"
    assert past["pe"]["now"] == 20.0


def test_trailing_eps_is_four_quarters_summed_not_one():
    # 四季各 2.5 → 滾動一年盈餘 10，股價 200 的本益比是 20 而不是 80
    f = fin(quads(2025, 2.5), [("2026-06-01", 200.0)] * 2)
    assert build_valuation(f, 5)["pe"]["now"] == 20.0


def test_price_change_splits_exactly_into_earnings_and_multiple():
    # 2023 四季各 1.25（滾動盈餘 5，2024-01-15 公布），2025 四季各 2.5（滾動盈餘 10，2026-01-15 公布）
    f = fin(quads(2023, 1.25, start_year=2023) + quads(2025, 2.5, start_year=2025),
            [("2024-06-01", 100.0), ("2026-06-01", 300.0)])
    s = build_valuation(f, 5)["split"]
    assert s["earnings"] == 100.0          # 滾動盈餘 5 → 10
    assert s["multiple"] == 50.0           # 本益比 20 → 30
    assert s["price"] == 200.0             # (1+1.0)×(1+0.5) - 1


def test_shorter_window_only_looks_at_that_window():
    # 五年前那個便宜的本益比不該出現在一年區間裡
    f = fin(quads(2025, 2.5, start_year=2019),
            [("2021-10-01", 50.0), ("2026-03-01", 300.0), ("2026-09-01", 400.0)])
    wide, narrow = build_valuation(f, 5), build_valuation(f, 1)
    assert wide["pe"]["low"] == 5.0
    assert narrow["pe"]["low"] == 30.0
    assert narrow["from"] == "2026-03-01"


def test_the_pe_range_and_the_split_cover_the_same_span():
    # 同一張卡裡兩個數字講不同時間，使用者會算出錯的結論
    f = fin(quads(2025, 2.5, start_year=2019),
            [("2021-10-01", 50.0), ("2026-03-01", 300.0), ("2026-09-01", 400.0)])
    for years in (1, 3, 5):
        d = build_valuation(f, years)
        assert d["from"] <= d["to"]
        assert d["split"]["price"] == pytest.approx(
            (400.0 / 50.0 - 1) * 100 if years == 5 else (400.0 / 300.0 - 1) * 100, abs=0.2)


def test_losing_money_raises_instead_of_returning_a_meaningless_number():
    with pytest.raises(RuntimeError):
        build_valuation(fin(quads(2025, -3.0), [("2026-06-01", 50.0)]), 5)


def test_revenue_growth_is_absent_on_the_oldest_year():
    f = fin(quads(2024, 0.25), [("2026-01-01", 10.0), ("2026-02-01", 10.0)],
            revenue={"2024-12-31": 110.0, "2023-12-31": 100.0},
            gross={"2024-12-31": 55.0, "2023-12-31": 40.0})
    m = build_valuation(f, 5)["margins"]
    assert m[0]["revenue_growth"] == 10.0
    assert "revenue_growth" not in m[1]
    assert m[0]["gross_margin"] == 50.0


def test_spark_points_carry_the_price_and_eps_that_produced_them():
    # 只送本益比數字的話，滑到某一點也答不出「當時股價多少」
    f = fin(quads(2025, 2.5, start_year=2019),
            [("2021-10-01", 50.0), ("2026-03-01", 300.0), ("2026-09-01", 400.0)])
    spark = build_valuation(f, 5)["pe"]["spark"]
    assert spark[0]["d"] == "2021-10-01"
    assert spark[-1] == {"d": "2026-09-01", "px": 400.0, "eps": 10.0, "pe": 40.0}
    assert all(round(s["px"] / s["eps"], 1) == s["pe"] for s in spark)


def ramp(pairs):
    """(年, 每季盈餘) 攤成四季。用來做「盈餘一年比一年暴衝」的假公司。"""
    return [(f"{y}-{m:02d}-01", e) for y, e in pairs for m in (3, 6, 9, 12)]


def test_earnings_outrunning_price_flags_the_percentile_as_untrustworthy():
    # 盈餘 10 倍、股價 4 倍 —— 本益比創新低是因為分母暴衝，不是股價便宜。
    # 不講清楚的話，「落在第 1%」會被讀成「不買是白痴」，而那通常是盈餘高峰
    q = ramp([(2021, 0.25), (2022, 0.5), (2023, 1.0), (2024, 1.5), (2025, 2.0), (2026, 2.5)])
    f = fin(q, [("2022-06-01", 100.0), ("2026-09-01", 400.0)])
    trap = build_valuation(f, 5)["pe"]["trap"]
    assert trap["price_x"] == 4.0
    assert trap["earnings_x"] > trap["price_x"] * 1.5
    assert trap["years"] == 5


def test_a_stock_whose_price_outran_its_earnings_gets_no_such_warning():
    # 股價漲得比盈餘快 —— 本益比是真的被推高的，百分位照它字面意思讀就對
    q = ramp([(2021, 1.0), (2022, 1.0), (2023, 1.0), (2024, 1.0), (2025, 1.0), (2026, 2.0)])
    f = fin(q, [("2022-06-01", 100.0), ("2026-09-01", 1000.0)])
    assert build_valuation(f, 5)["pe"]["trap"] is None


def test_modest_earnings_growth_is_not_a_trap_even_if_it_beats_the_price():
    # 盈餘 +60%、股價持平。方向對，但幅度小到不足以扭曲百分位，別亂嚇人
    q = ramp([(2021, 1.0), (2022, 1.0), (2023, 1.0), (2024, 1.0), (2025, 1.0), (2026, 1.6)])
    f = fin(q, [("2022-06-01", 100.0), ("2026-09-01", 100.0)])
    assert build_valuation(f, 5)["pe"]["trap"] is None


def growing(rates, start=1.0):
    """每季盈餘照 rates 逐季乘上去，用來擺佈滾動一年盈餘的年增率。"""
    eps, out = start, []
    for i, r in enumerate(rates):
        y, m = 2020 + i // 4, (i % 4 + 1) * 3
        out.append((f"{y}-{m:02d}-01", eps))
        eps *= r
    return out


def test_shrinking_earnings_turns_the_denominator_check_red():
    # 盈餘一路縮：分母在變小，本益比會自己彈回去 —— 這一題必須亮紅燈
    q = growing([1.0] * 4 + [0.9] * 8)
    d = build_valuation(fin(q, [("2025-01-01", 50.0), ("2026-01-01", 50.0)]), 5)
    growth = next(r for r in d["quality"] if r["key"] == "growth")
    assert growth["ok"] is False and growth["value"] < 0


def test_earnings_that_barely_moved_get_no_verdict():
    # 幾乎沒動就不給方向。財報數字本來就會小幅晃動，硬給結論是在讀雜訊
    q = growing([1.0] * 12)
    d = build_valuation(fin(q, [("2025-01-01", 50.0), ("2026-01-01", 50.0)]), 5)
    assert next(r for r in d["quality"] if r["key"] == "growth")["ok"] is None


def test_a_question_with_no_data_says_so_instead_of_guessing():
    # 沒給損益表，營收和淨利率兩題就該老實說答不了，不是預設成過關
    q = growing([1.1] * 12)
    d = build_valuation(fin(q, [("2025-01-01", 50.0), ("2026-01-01", 50.0)]), 5)
    for key in ("revenue", "margin"):
        row = next(r for r in d["quality"] if r["key"] == key)
        assert row["ok"] is None and row["value"] is None


def test_every_question_is_answered_exactly_once():
    from app.config import QUALITY_CHECKS
    q = growing([1.1] * 12)
    d = build_valuation(fin(q, [("2025-01-01", 50.0), ("2026-01-01", 50.0)]), 5)
    assert [r["key"] for r in d["quality"]] == list(QUALITY_CHECKS)


def test_fair_price_is_the_pe_range_multiplied_by_todays_earnings():
    """低／中／高三個價，就是三個本益比乘上同一個盈餘。不給單一目標價。"""
    q = quads(2025, 2.5)                                  # 滾動一年盈餘固定 10
    f = fin(q, [("2026-01-02", 100.0), ("2026-02-02", 300.0), ("2026-03-02", 200.0)])
    d = build_valuation(f, 5)
    fair = d["fair"]
    assert fair["eps"] == 10.0 and fair["now"] == 200.0
    assert fair["low"] == {"pe": 10.0, "price": 100.0, "gap": -50.0}
    assert fair["high"] == {"pe": 30.0, "price": 300.0, "gap": 50.0}
    assert fair["mid"]["price"] == 200.0 and fair["mid"]["gap"] == 0.0


def test_every_check_says_which_two_periods_it_compared():
    """不寫出「拿哪兩段比」，看的人沒辦法判斷這個數字在答什麼問題。"""
    q = growing([1.1] * 12)
    d = build_valuation(fin(q, [("2025-01-01", 50.0), ("2026-01-01", 50.0)]), 5)
    assert all(r["basis"] and "vs" in r["basis"] for r in d["quality"])


def test_the_checks_ignore_the_lookback_years():
    # 回看幾年管的是本益比區間有多寬，不是公司現在的體質。兩件事，不准互相污染
    q = growing([1.1] * 20)
    f = fin(q, [("2025-01-01", 50.0), ("2026-01-01", 50.0)])
    assert build_valuation(f, 1)["quality"] == build_valuation(f, 5)["quality"]


def test_the_pe_extremes_carry_the_day_they_happened():
    """1 年和 5 年區間一模一樣的時候，沒有日期就看起來像拉桿壞了。"""
    q = quads(2025, 2.5)                                  # 滾動一年盈餘固定 10
    f = fin(q, [("2026-01-02", 100.0), ("2026-02-02", 300.0), ("2026-03-02", 200.0)])
    p = build_valuation(f, 5)["pe"]
    assert p["low_at"] == "2026-01-02" and p["high_at"] == "2026-02-02"
