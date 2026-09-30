from app.config import BENCHMARK_SYMBOL, RISK_EXTRA_SYMBOLS, RISK_RATIOS, SECTOR_ETF
from app.rotation import build_rotation

DAYS = 300


def flat(value=100.0):
    return [value] * DAYS


def ramp(start, end, days=DAYS):
    """線性從 start 走到 end。用來做「這個板塊一路變強」的假資料。"""
    return [start + (end - start) * i / (days - 1) for i in range(days)]


def market(**overrides):
    closes = {t: flat() for t in
              (BENCHMARK_SYMBOL, *SECTOR_ETF.values(), *RISK_EXTRA_SYMBOLS)}
    closes.update(overrides)
    return closes


def test_every_configured_ratio_gets_a_row():
    """設定裡加了比值，抓資料的代號清單卻忘了加，會靜默少一列 —— 這裡讓它爆掉。"""
    d = build_rotation(market(), "2026-09-25", "SPY")
    assert len(d["risk"]) == len(RISK_RATIOS)
    for spec in RISK_RATIOS:
        assert spec["up"] in RISK_EXTRA_SYMBOLS or spec["up"] in SECTOR_ETF.values()
        assert spec["down"] in RISK_EXTRA_SYMBOLS or spec["down"] in SECTOR_ETF.values()


def test_a_sector_beating_the_market_is_ranked_first():
    d = build_rotation(market(XLK=ramp(100, 200)), "2026-09-25", "SPY")
    assert d["sectors"][0]["etf"] == "XLK"
    assert d["sectors"][0]["tone"] == "in"
    assert d["sectors"][-1]["tone"] == "flat"


def test_a_sector_rising_exactly_as_fast_as_the_market_is_not_called_strong():
    # 整袋錢變大的時候大家都漲。漲幅不算數，只有「贏過大盤」才算錢搬進來
    both = ramp(100, 200)
    d = build_rotation(market(SPY=both, XLK=both), "2026-09-25", "SPY")
    tech = next(s for s in d["sectors"] if s["etf"] == "XLK")
    assert tech["tone"] == "flat"
    assert abs(tech["rs"]) < 0.01
    assert tech["returns"]["1月"] > 3  # 漲很多，但那是整個市場的事


def test_a_sector_rising_while_the_market_rises_faster_is_losing_money():
    d = build_rotation(market(SPY=ramp(100, 300), XLK=ramp(100, 150)), "2026-09-25", "SPY")
    tech = next(s for s in d["sectors"] if s["etf"] == "XLK")
    assert tech["returns"]["1月"] > 0  # 股價是漲的
    assert tech["tone"] == "out"  # 但錢正在離開


def test_only_one_winner_is_reported_as_a_narrow_market():
    d = build_rotation(market(XLK=ramp(100, 200)), "2026-09-25", "SPY")
    assert d["verdict"]["code"] == "narrow"
    assert "科技" in d["verdict"]["text"]


def test_many_winners_is_reported_as_a_normal_rotation():
    wide = {t: ramp(100, 120 + i) for i, t in
            enumerate(("XLK", "XLY", "XLP", "XLV", "XLF", "XLE"))}
    d = build_rotation(market(**wide), "2026-09-25", "SPY")
    assert d["verdict"]["code"] == "broad"


def test_risk_appetite_reads_the_ratio_not_either_leg():
    # 兩邊都在漲，但 XLY 漲得快 —— 比值上升，錢在冒險
    d = build_rotation(market(XLY=ramp(100, 200), XLP=ramp(100, 120)), "2026-09-25", "SPY")
    appetite = next(r for r in d["risk"] if r["pair"] == "XLY÷XLP")
    assert appetite["tone"] == "in"
    assert appetite["moves"]["3月"] > 0


def test_a_missing_benchmark_raises_instead_of_inventing_a_baseline():
    try:
        build_rotation({"XLK": flat()}, "2026-09-25", "SPY")
    except RuntimeError as exc:
        assert "大盤" in str(exc)
    else:
        raise AssertionError("沒有大盤還算得出相對強度，那是假的")


def test_a_shorter_window_changes_the_numbers_but_not_the_row_order():
    """前面 280 天科技一路強、最後 20 天金融暴衝。

    換區間要換的是「數字」，不是「誰排第幾列」—— 列會跳動就追不到自己在看的那一條。
    """
    tech = ramp(100, 400)
    fin = flat()[:280] + [100 + i * 0.9 for i in range(20)]
    long_ = build_rotation(market(XLK=tech, XLF=fin), "2026-09-25", "SPY", 252)
    short = build_rotation(market(XLK=tech, XLF=fin), "2026-09-25", "SPY", 5)
    best = lambda d: max(d["sectors"], key=lambda s: s["rs"])["etf"]
    assert best(long_) == "XLK"
    assert best(short) == "XLF"
    assert short["range_label"] == "1 週"


def test_the_row_order_is_the_config_order_and_never_gets_sorted():
    # 排序寫回來就會讓畫面每換一次區間跳一次位置 —— 這條測試就是為了擋那個
    for days in (5, 21, 252):
        d = build_rotation(market(XLK=ramp(100, 400)), "2026-09-25", "SPY", days)
        assert [s["etf"] for s in d["sectors"]] == list(SECTOR_ETF.values())


def test_a_window_longer_than_the_data_raises_instead_of_using_the_oldest_row():
    # 資料只有 300 天，卻要算 252 天以外的區間 —— 不准拿最舊那筆硬湊成一個假數字
    try:
        build_rotation(market(), "2026-09-25", "SPY", DAYS + 10)
    except RuntimeError:
        return
    raise AssertionError("資料不夠還算得出來，那是編的")


def test_risk_ratios_follow_the_same_window_as_the_sectors():
    """拉桿只換上半部的話，下半部不管拉到哪都長一樣 —— 使用者會以為畫面壞了。"""
    closes = market(XLY=ramp(100, 200), XLP=flat())
    short = build_rotation(closes, "2026-09-25", "SPY", 5)
    long_ = build_rotation(closes, "2026-09-25", "SPY", 252)
    pick = lambda d: next(r for r in d["risk"] if r["pair"] == "XLY÷XLP")["move"]
    assert pick(long_) > pick(short) > 0


def test_the_ratio_line_starts_at_zero_and_ends_on_the_number_next_to_it():
    """線的終點必須就是旁邊那個百分比，不然一張圖一個數字在講兩件事。"""
    d = build_rotation(market(XLY=ramp(100, 200), XLP=flat()), "2026-09-25", "SPY", 20)
    r = next(x for x in d["risk"] if x["pair"] == "XLY÷XLP")
    assert len(r["line"]) == 21
    assert r["line"][0] == 0.0
    assert abs(r["line"][-1] - r["move"]) < 0.05


def test_the_ratio_line_follows_the_slider():
    """線不跟著拉桿變長的話，拉桿對下半部就是死的。"""
    closes = market(XLY=ramp(100, 200), XLP=flat())
    length = lambda days: len(next(
        x for x in build_rotation(closes, "2026-09-25", "SPY", days)["risk"]
        if x["pair"] == "XLY÷XLP")["line"])
    assert length(130) == 131 and length(5) == 6


def test_two_ratios_with_the_same_ending_can_still_have_different_lines():
    """一路慢慢漲，跟先衝上去再摔回來 —— 期末數字一樣，但意思完全相反。
    只給一個百分比的話這兩件事長得一模一樣，這就是要畫線的理由。"""
    steady = market(XLY=ramp(100, 110, 300), XLP=flat())
    spike = market(XLY=ramp(100, 140, 280) + ramp(140, 110, 20), XLP=flat())
    pick = lambda c: next(x for x in build_rotation(c, "2026-09-25", "SPY", 40)["risk"]
                          if x["pair"] == "XLY÷XLP")
    a, b = pick(steady), pick(spike)
    assert max(a["line"]) == a["line"][-1]  # 一路往上，最高點就是終點
    assert max(b["line"]) > b["line"][-1]   # 中途更高，後來摔回來


def test_rrg_puts_every_sector_on_the_same_two_axes():
    """十一個板塊不是十一個維度 —— 每一個都只拿到 x、y 兩個座標。"""
    d = build_rotation(market(), "2026-09-25", "SPY")
    assert len(d["rrg"]) == len(SECTOR_ETF)
    for p in d["rrg"]:
        assert set(p) == {"etf", "label", "x", "y", "quadrant", "tail"}
        assert isinstance(p["x"], float) and isinstance(p["y"], float)


def test_a_sector_identical_to_the_market_sits_on_the_origin():
    # 跟大盤一模一樣就是原點。強弱都談不上，不該被硬塞進四個象限裡任何一個
    d = build_rotation(market(), "2026-09-25", "SPY")
    p = d["rrg"][0]
    assert (abs(p["x"]), abs(p["y"])) == (0.0, 0.0)
    assert p["quadrant"] == "flat"


def test_a_sector_pulling_away_from_the_market_lands_in_the_leading_quadrant():
    # 注意：「一路直線上漲」不是領先。直線的話百分比成長會逐年遞減，動能是負的 ——
    # 領先要的是「正在加速」，所以假資料必須是最近才起飛
    d = build_rotation(market(XLK=[100.0] * 280 + ramp(100, 160, 20)), "2026-09-25", "SPY")
    tech = next(p for p in d["rrg"] if p["etf"] == "XLK")
    assert tech["x"] > 0 and tech["y"] > 0 and tech["quadrant"] == "lead"


def test_a_steady_climb_is_not_mistaken_for_acceleration():
    """一路等速上漲的板塊，動能不該是正的 —— 漲得久不等於漲得越來越快。"""
    d = build_rotation(market(XLK=ramp(100, 300)), "2026-09-25", "SPY")
    tech = next(p for p in d["rrg"] if p["etf"] == "XLK")
    assert tech["x"] > 0 and tech["y"] < 0


def test_a_sector_that_already_peaked_is_called_weakening_not_leading():
    """還在大盤之上、但動能已經轉負 —— 這就是「錢開始撤了」，跟「領先」是兩件事。"""
    rise_then_stall = ramp(100, 200, 270) + [200.0] * (DAYS - 270)
    d = build_rotation(market(XLE=rise_then_stall), "2026-09-25", "SPY")
    e = next(p for p in d["rrg"] if p["etf"] == "XLE")
    assert e["x"] > 0 and e["y"] < 0 and e["quadrant"] == "weaken"


def test_a_sector_falling_off_a_cliff_lands_in_the_lagging_quadrant():
    d = build_rotation(market(XLU=[100.0] * 280 + ramp(100, 70, 20)), "2026-09-25", "SPY")
    u = next(p for p in d["rrg"] if p["etf"] == "XLU")
    assert u["x"] < 0 and u["y"] < 0 and u["quadrant"] == "lag"


def test_the_end_of_the_tail_is_the_same_point_whatever_the_slider_says():
    """終點是「今天在哪」，跟「往回看多久」無關。兩格看起來不一樣的話，
    使用者會以為同一個板塊換個區間就走了不同的路。"""
    closes = market(XLK=ramp(100, 300))
    ends = {(p["x"], p["y"])
            for d in (5, 20, 65, 130)
            for p in build_rotation(closes, "2026-09-25", "SPY", d)["rrg"] if p["etf"] == "XLK"}
    assert len(ends) == 1


def test_a_short_tail_is_literally_the_tail_end_of_a_long_one():
    """3 週的起點必須是 4 週走過的其中一個點 —— 兩條路是同一條路的不同長度，
    不是兩條不同的路。平均切成固定份數的話，兩邊取到的點根本不重合。"""
    closes = market(XLK=ramp(100, 300))
    tail = lambda d: [(t["x"], t["y"]) for p in build_rotation(closes, "2026-09-25", "SPY", d)["rrg"]
                      if p["etf"] == "XLK" for t in p["tail"]]
    long_, short = tail(20), tail(15)
    assert len(short) < len(long_)
    assert short == long_[len(long_) - len(short):]
    assert short[0] in long_


def test_the_slider_makes_the_tail_cover_more_ground():
    """尾巴不跟著拉桿變長的話，拉桿對上面那張圖就是死的。"""
    closes = market(XLK=ramp(100, 300))
    span = lambda days: max(
        t["x"] for t in next(p for p in build_rotation(closes, "2026-09-25", "SPY", days)["rrg"]
                             if p["etf"] == "XLK")["tail"]
    ) - min(
        t["x"] for t in next(p for p in build_rotation(closes, "2026-09-25", "SPY", days)["rrg"]
                             if p["etf"] == "XLK")["tail"])
    assert span(130) > span(5)


def test_the_tail_always_ends_on_the_dot_you_can_see():
    """尾巴最後一點必須就是現在的位置，不然軌跡會跟點對不起來。"""
    d = build_rotation(market(XLK=ramp(100, 300)), "2026-09-25", "SPY", 63)
    for p in d["rrg"]:
        assert (p["tail"][-1]["x"], p["tail"][-1]["y"]) == (p["x"], p["y"])
