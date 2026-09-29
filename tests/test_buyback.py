from datetime import date, timedelta

import pytest

from app.buyback import build_buyback


def quarters(n, amount):
    """由新到舊 n 季，每季花 amount 買回。"""
    d = date(2026, 9, 30)
    return [{"period": (d - timedelta(days=91 * i)).isoformat(), "amount": amount} for i in range(n)]


def shares(start_count, per_year, years=5.0):
    """從 start_count 開始，每年乘 per_year 倍，按季取樣。"""
    d0 = date(2026, 9, 30) - timedelta(days=round(365.25 * years))
    n = int(years * 4)
    return [{"period": (d0 + timedelta(days=91 * i)).isoformat(),
             "count": start_count * per_year ** (91 * i / 365.25)} for i in range(n + 1)]


def raw(spend, sh, price=100.0):
    return {"spend": spend, "shares": sh, "price": price}


def test_shrinking_share_count_is_a_real_buyback():
    d = build_buyback(raw(quarters(12, 1e9), shares(1e9, 0.97)), 1e9)
    assert d["verdict"]["code"] == "real"
    assert d["shrink"] == pytest.approx(-3.0, abs=0.1)
    assert d["year"] == 4e9


def test_spending_a_fortune_while_the_share_count_stands_still_is_called_out():
    # NVDA 實測：一年花 553 億美金回購，股數只少 0.69% —— 錢繞一圈進了員工口袋
    d = build_buyback(raw(quarters(12, 1e10), shares(1e9, 0.999)), 1e9)
    assert d["verdict"]["code"] == "offset"
    assert "抵銷" in d["verdict"]["text"]


def test_a_company_that_barely_buys_back_is_not_accused_of_anything():
    d = build_buyback(raw(quarters(12, 1e5), shares(1e9, 1.0)), 1e9)
    assert d["verdict"]["code"] == "none"


def test_the_yield_is_the_last_four_quarters_over_market_cap():
    # 12 季都算進去的話，殖利率會膨脹成三倍。只能看最近四季
    d = build_buyback(raw(quarters(12, 1e9), shares(1e9, 1.0), price=400.0), 1e9)
    assert d["year"] == 4e9
    assert d["market_cap"] == 4e11
    assert d["yield"] == 1.0


def test_a_growing_share_count_reports_a_positive_change():
    # 股數變多是真的會發生（增資、發股票給員工）。不准偷偷取絕對值蓋掉方向
    d = build_buyback(raw(quarters(12, 1e9), shares(1e9, 1.05)), 1e9)
    assert d["shrink"] > 4.0
    assert d["verdict"]["code"] != "real"


def test_too_few_quarters_raises_instead_of_guessing_a_year():
    with pytest.raises(RuntimeError):
        build_buyback(raw(quarters(3, 1e9), shares(1e9, 0.97)), 1e9)


def test_less_than_a_year_of_share_history_raises():
    with pytest.raises(RuntimeError):
        build_buyback(raw(quarters(12, 1e9), shares(1e9, 0.97, years=0.25)), 1e9)


def annual(rows):
    return [{"year": y, "amount": a, "shares": s} for y, a, s in rows]


def test_the_annual_table_shows_spend_and_share_count_side_by_side():
    # CB 實測：2025 花的錢是 2024 的兩倍，股數卻少減 —— 只看金額完全看不出來
    d = build_buyback(
        raw(quarters(12, 1e9), shares(1e9, 0.98)) | {
            "annual": annual([(2023, 24e8, 4.08e8), (2024, 18e8, 4.03e8), (2025, 37e8, 3.99e8)])},
        1e9)
    rows = d["annual"]
    assert [r["year"] for r in rows] == [2023, 2024, 2025]
    assert rows[0]["change"] is None  # 沒有前一年可以比，就不准編一個出來
    # 錢花了兩倍多，股數卻少減 —— 兩欄並排才看得出這件事
    assert rows[2]["amount"] > rows[1]["amount"] * 2
    assert abs(rows[2]["change"]) < abs(rows[1]["change"])


def test_a_year_where_the_share_count_grew_is_not_hidden():
    # NVDA 2024 實測：花了 95 億回購，股數反而多了 2.9%
    d = build_buyback(
        raw(quarters(12, 1e9), shares(1e9, 0.99)) | {
            "annual": annual([(2023, 100e8, 247e8), (2024, 95e8, 254.1e8)])},
        1e9)
    assert d["annual"][1]["change"] > 2.5


def test_missing_annual_history_yields_an_empty_table_not_a_crash():
    d = build_buyback(raw(quarters(12, 1e9), shares(1e9, 0.98)), 1e9)
    assert d["annual"] == []


def test_the_boost_is_the_mirror_of_the_share_count_change():
    # 股數年減 3%，每股盈餘就被推高 3/(97) ≈ +3.1%。不是直接把負號拿掉
    d = build_buyback(raw(quarters(12, 1e9), shares(1e9, 0.97)), 1e9)
    assert d["boost"] == pytest.approx(3.1, abs=0.1)


def test_a_growing_share_count_gives_a_negative_boost():
    # 股數變多就是稀釋，推力是負的。不准因為「這是回購卡」就寫成正的
    d = build_buyback(raw(quarters(12, 1e9), shares(1e9, 1.05)), 1e9)
    assert d["boost"] < 0


def test_the_dividend_yield_annualises_the_latest_payment():
    # 每季配 1 元、股價 100 → 4%。用「過去一年加總」的話，缺一筆就會變 3%
    pay = [{"date": (date(2026, 9, 11) - timedelta(days=91 * i)).isoformat(), "amount": 1.0}
           for i in range(5)][::-1]
    d = build_buyback({**raw(quarters(12, 1e9), shares(1e9, 1.0)), "dividends": pay}, 1e9)
    assert d["dividend_yield"] == pytest.approx(4.0, abs=0.05)
    assert d["total_yield"] == pytest.approx(d["yield"] + 4.0, abs=0.05)


def test_a_company_that_pays_nothing_reports_no_dividend_yield():
    # 不配息就是 None，不是 0 —— 畫面上要能分辨「沒配」和「配了但很少」
    d = build_buyback(raw(quarters(12, 1e9), shares(1e9, 1.0)), 1e9)
    assert d["dividend_yield"] is None
    assert d["total_yield"] == d["yield"]
