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
    assert short["range_label"] == "5 天"


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
