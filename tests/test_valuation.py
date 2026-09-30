from datetime import date, timedelta

import pytest

from app.config import SLICE_CONCLUSIONS, VALUATION_TABLE_ROWS
from app.valuation import build_valuation, price_verdict, support_verdict


def fin(quarters, closes, revenue=None, gross=None, net=None, releases=None, shares=None):
    return {"quarters": [{"period": p, "eps": v} for p, v in quarters],
            "closes": [{"date": d, "close": c} for d, c in closes],
            "releases": releases if releases is not None else announced(quarters),
            "revenue": revenue or {}, "gross": gross or {}, "net": net or {},
            "shares": shares or {}}


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
    assert m[0]["d"]["revenue"] == 10.0
    assert m[1]["d"]["revenue"] is None
    assert m[0]["gross_margin"] == 50.0


def test_net_growth_is_skipped_when_last_year_lost_money():
    """去年是負的時候，(今年-去年)/|去年| 的符號會跟直覺反過來 ——
    虧 100 變賺 10 會算成「-110%」。那種年度寧可不給數字。"""
    f = fin(quads(2024, 0.25), [("2026-01-01", 10.0), ("2026-02-01", 10.0)],
            revenue={"2024-12-31": 110.0, "2023-12-31": 100.0, "2022-12-31": 90.0},
            net={"2024-12-31": 10.0, "2023-12-31": -5.0, "2022-12-31": 8.0})
    m = build_valuation(f, 5)["margins"]
    assert m[0]["d"]["net"] is None          # 去年虧錢
    assert m[1]["d"]["net"] == -162.5        # -5 vs 8，正常的由賺轉虧
    assert m[2]["d"]["net"] is None          # 最舊那年沒有前一年可比


# ── 財年表格：單位規則 ───────────────────────────────────────

def table(**kw):
    """兩個財年的最小資料，數字挑成手算得出來的。"""
    base = dict(
        revenue={"2025-12-31": 200.0, "2024-12-31": 100.0},
        gross={"2025-12-31": 80.0, "2024-12-31": 30.0},          # 毛利率 40% vs 30%
        net={"2025-12-31": 20.0, "2024-12-31": 20.0},            # 淨利率 10% vs 20%
        shares={"2025-12-31": 200.0, "2024-12-31": 100.0})
    base.update(kw)
    return build_valuation(
        fin(quads(2025, 2.5), [("2026-01-01", 10.0), ("2026-02-01", 10.0)], **base), 5)


def test_rates_change_in_points_and_amounts_change_in_percent():
    """這張表最容易做錯的一件事，也是加它的全部理由。

    淨利率 20% → 10% 掉的是「10 個百分點」，不是「-50%」。寫成 -50% 會被讀成
    「公司少賺一半」—— 但公司賺的錢一毛沒少（20 → 20），變的是營收。
    兩個 % 不是同一個意思，混用就是在騙人。
    """
    d = table()
    assert d["margins"][0]["d"]["net_margin"] == -10.0      # 相減，不是 -50.0
    assert d["margins"][0]["d"]["gross_margin"] == 10.0     # 相減，不是 +33.3
    assert d["margins"][0]["d"]["revenue"] == 100.0         # 相除
    assert d["margins"][0]["d"]["net"] == 0.0               # 賺的錢真的沒變


def test_every_row_in_the_spec_gets_a_delta_key():
    """設定裡加一列、算式卻沒跟上，畫面會靜默少一格變化 —— 不會報錯，只會空白。"""
    d = table()
    for key, _, _, _ in VALUATION_TABLE_ROWS:
        assert key in d["margins"][0]
        assert key in d["margins"][0]["d"]


def test_the_delta_unit_is_never_decided_by_the_frontend():
    """單位跟著列定義一起送出去。前端自己記一份的話，兩邊遲早會對不上。"""
    units = {key: unit for key, _, _, unit in VALUATION_TABLE_ROWS}
    assert units == {"revenue": "pct", "net": "pct", "gross_margin": "pp",
                     "net_margin": "pp", "shares": "pct", "eps": "pct"}


# ── 切蛋糕：每股盈餘 = 餅 ÷ 份數 ─────────────────────────────

def test_the_three_numbers_multiply_back_together():
    """三個百分比必須真的對得起來。對不起來就不是拆解，是三個各講各話的數字。"""
    s = table()["slice"]
    assert (1 + s["net"]["pct"] / 100) / (1 + s["shares"]["pct"] / 100) - 1 == \
        pytest.approx(s["eps"]["pct"] / 100, abs=0.002)


def test_issuing_shares_shows_up_as_the_gap_between_profit_and_per_share():
    """公司賺的錢沒變，股數翻倍 —— 每股盈餘砍半。這 50 個百分點全是股數造成的。"""
    s = table()
    assert s["slice"]["net"]["pct"] == 0.0
    assert s["slice"]["shares"]["pct"] == 100.0
    assert s["slice"]["eps"]["pct"] == -50.0
    assert s["slice"]["gap"] == -50.0
    assert s["slice"]["verdict"] == "dilute"


def test_buying_back_shares_flatters_per_share_earnings():
    """回購會把每股盈餘墊高。不標出來，你會以為公司成長得比實際快。"""
    s = table(shares={"2025-12-31": 80.0, "2024-12-31": 100.0})["slice"]
    assert s["shares"]["pct"] == -20.0
    assert s["gap"] == 25.0            # 賺的錢沒變，每股盈餘卻多了 25%
    assert s["verdict"] == "buyback"


def test_a_share_count_that_barely_moved_is_called_flat():
    """股數一年動 1% 是員工配股的正常呼吸，不是稀釋。硬要解讀就是在製造雜訊。"""
    s = table(shares={"2025-12-31": 101.0, "2024-12-31": 100.0})["slice"]
    assert s["verdict"] == "flat"


def test_a_buyback_propping_up_a_shrinking_pie_is_not_a_thumbs_up():
    """公司少賺，每股盈餘卻變多 —— 那是回購撐的。給 👍 等於把最該警覺的情況說成好消息。"""
    s = table(net={"2025-12-31": 18.0, "2024-12-31": 20.0},
              shares={"2025-12-31": 50.0, "2024-12-31": 100.0})["slice"]
    assert s["net"]["pct"] < 0 and s["eps"]["pct"] > 0
    assert s["conclusion"] == "propped"


def test_both_going_up_is_the_only_clean_thumbs_up():
    s = table(net={"2025-12-31": 30.0, "2024-12-31": 20.0},
              shares={"2025-12-31": 100.0, "2024-12-31": 100.0})["slice"]
    assert s["conclusion"] == "both_up"


def test_profit_up_but_your_slice_down_is_dilution_not_good_news():
    s = table(net={"2025-12-31": 22.0, "2024-12-31": 20.0})["slice"]
    assert s["net"]["pct"] > 0 and s["eps"]["pct"] < 0
    assert s["conclusion"] == "diluted"


def test_both_going_down_is_a_thumbs_down():
    s = table(net={"2025-12-31": 10.0, "2024-12-31": 20.0},
              shares={"2025-12-31": 100.0, "2024-12-31": 100.0})["slice"]
    assert s["conclusion"] == "both_down"


def test_a_per_share_number_that_barely_moved_gets_no_direction():
    """每股盈餘動 1% 不是好消息也不是壞消息。硬給方向就是在製造訊號。"""
    s = table(net={"2025-12-31": 20.2, "2024-12-31": 20.0},
              shares={"2025-12-31": 100.0, "2024-12-31": 100.0})["slice"]
    assert s["conclusion"] == "flat"


def test_every_conclusion_the_backend_can_emit_has_wording():
    """後端算得出來、config 裡卻沒有那句話 —— 畫面會安靜地少一行，沒人會發現。"""
    assert set(SLICE_CONCLUSIONS) == {"flat", "both_up", "propped", "diluted", "both_down"}
    assert all(len(v) == 2 and v[0] and v[1] for v in SLICE_CONCLUSIONS.values())


def test_no_share_data_means_no_slice_at_all():
    """Yahoo 這個欄位沒有官方合約，哪天消失就是整塊不畫 —— 不是畫成 0 或 undefined。"""
    assert table(shares={})["slice"] is None
    assert table()["margins"][0]["eps"] is not None


def test_a_year_that_swung_out_of_a_loss_gets_no_slice():
    """去年虧錢，百分比的符號會反過來。與其給一個 -118% 的垃圾數字，不如整塊不畫。"""
    assert table(net={"2025-12-31": 20.0, "2024-12-31": -5.0})["slice"] is None


def test_one_fiscal_year_alone_has_nothing_to_compare():
    assert table(revenue={"2025-12-31": 200.0}, gross={}, net={"2025-12-31": 20.0},
                 shares={"2025-12-31": 200.0})["slice"] is None


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


def sup(earnings, multiple, bad, sliced=None):
    """第二區結論的最小輸入：漲幅怎麼拆、四題亮幾盞紅燈。"""
    quality = [{"ok": False}] * bad + [{"ok": True}] * (4 - bad)
    return support_verdict({"earnings": earnings, "multiple": multiple}, quality, sliced)


def test_the_section_two_verdict_crosses_both_sub_sections_not_just_one():
    """只看誰撐的，會把「過去靠本業、現在在縮」講成好消息；只看四題，會把
    「公司還在長但漲的全是情緒」講成沒事。兩個軸都要進來，才會有這四種答案。"""
    assert sup(70.8, 39.7, 0)["key"] == "earned_solid"
    assert sup(70.8, 39.7, 2)["key"] == "earned_slipping"
    assert sup(10.0, 90.0, 0)["key"] == "mood_solid"
    assert sup(10.0, 90.0, 3)["key"] == "mood_slipping"


def test_only_a_red_light_counts_as_a_red_light():
    """四題裡 ok=None 是「資料不足」或「沒動」，那不是壞消息。
    算進來的話，一檔資料不全的股票會被判成兩邊都在爛。"""
    v = support_verdict({"earnings": 50.0, "multiple": 10.0},
                        [{"ok": None}] * 4, None)
    assert v["bad"] == 0 and v["key"] == "earned_solid"


def test_only_the_worst_box_gets_a_thumbs_down():
    faces = {sup(*a)["face"] for a in [(70.8, 39.7, 2), (10.0, 90.0, 0)]}
    assert faces == {"⚠️"}
    assert sup(70.8, 39.7, 0)["face"] == "👍"
    assert sup(10.0, 90.0, 3)["face"] == "👎"


def test_a_buyback_propped_per_share_number_is_called_out_even_on_a_good_verdict():
    """👍 不能把「每股盈餘是回購撐的」蓋掉 —— 那一截不會一直有。"""
    v = sup(70.8, 39.7, 0, {"conclusion": "propped"})
    assert v["face"] == "👍" and "回購撐起來的" in v["note"]
    assert sup(70.8, 39.7, 0, {"conclusion": "both_up"})["note"] == ""
    assert sup(70.8, 39.7, 0, None)["note"] == ""


def test_the_red_light_count_in_the_text_matches_the_one_that_was_counted():
    """句子裡的「有 N 題亮紅燈」和實際數出來的 N 分兩條路算，遲早會對不上。"""
    v = sup(10.0, 90.0, 3)
    assert f"有 {v['bad']} 題亮紅燈" in v["text"]


def test_the_section_two_verdict_shows_the_two_numbers_it_compared():
    """「主要是本業撐的」在 70.8 vs 39.7 和 50.0 vs 49.9 會寫成同一句話。
    把兩個數字一起送出去，看的人自己判斷得了差多少。"""
    v = sup(50.0, 49.9, 0)
    assert v["earnings"] == 50.0 and v["multiple"] == 49.9


def test_the_price_verdict_reads_straight_off_the_percentile():
    """貴不貴只看一件事：落在自己過去的第幾 %。門檻挪動時這條會先叫。"""
    assert price_verdict(95, 5, None)["label"] == "偏貴"
    assert price_verdict(80, 5, None)["label"] == "偏貴"
    assert price_verdict(70, 5, None)["label"] == "有點偏貴"
    assert price_verdict(50, 5, None)["label"] == "跟自己平常差不多"
    assert price_verdict(30, 5, None)["label"] == "有點偏便宜"
    assert price_verdict(5, 5, None)["label"] == "偏便宜"


def test_a_trap_overrides_the_cheap_verdict_instead_of_sitting_next_to_it():
    """盈餘暴衝造成的第 1%，印「🟢 偏便宜」比不印還糟 —— 那是把最危險的情況說成機會。"""
    v = price_verdict(1, 5, {"years": 5, "earnings_x": 10.0, "price_x": 4.0})
    assert v["face"] == "⚠️"
    assert "便宜" in v["label"] and "偏便宜" != v["label"]


def test_the_verdict_never_claims_to_know_what_the_company_is_worth():
    """這張卡沒有同業數字、也沒有現金流模型。講「值多少」「比別家便宜」就是憑空生出來的。"""
    for pct in (0, 25, 50, 75, 100):
        v = price_verdict(pct, 5, None)
        assert "不是跟別家公司比" in v["tail"] and "不是在說它應該值多少錢" in v["tail"]
        assert "目標價" not in v["text"] and "應該漲" not in v["text"]


def test_every_percentile_from_zero_to_a_hundred_gets_a_verdict():
    """百分位是 round() 出來的整數，0 和 100 都到得了。漏接就是整區結論不見。"""
    assert all(price_verdict(p, 5, None)["label"] for p in range(101))


def test_the_verdict_travels_with_the_percentile_it_was_computed_from():
    """結論和百分位分兩條路送到畫面上的話，遲早會出現「第 90% ＋ 偏便宜」。"""
    q = ramp([(2021, 1.0), (2022, 1.0), (2023, 1.0), (2024, 1.0), (2025, 1.0), (2026, 1.0)])
    pe = build_valuation(fin(q, [("2022-06-01", 100.0), ("2026-09-01", 300.0)]), 5)["pe"]
    assert pe["verdict"]["pct"] == pe["pct"]
    assert pe["verdict"]["years"] == 5


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
