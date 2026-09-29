from app.analyze import build_report, dedupe, verdict_for

BARS = [
    {"date": "2026-09-14", "close": 100.0},
    {"date": "2026-09-15", "close": 107.0},  # +7.00% → 異動
    {"date": "2026-09-16", "close": 106.0},  # -0.93% → 不是異動
    {"date": "2026-09-17", "close": 101.0},  # -4.72% → 異動
]


def art(date_, hour, headline, source):
    return {
        "date": date_,
        "ts": f"{date_}T{hour:02d}:00:00+00:00",
        "headline": headline,
        "source": source,
        "url": "",
        "summary": "",
        "provider": "test",
    }


ARTICLES = [
    art("2026-09-15", 12, "ACME wins huge supply deal", "Reuters"),
    art("2026-09-14", 20, "ACME to announce partnership", "CNBC"),  # 收盤後 → 歸 9/15
    art("2026-09-15", 12, "ACME Wins Huge Supply Deal", "Yahoo"),  # 與第一篇重複
    art("2026-09-16", 9, "Analyst raises ACME price target to 130", "Barrons"),
    art("2026-09-16", 10, "ACME opens new office in Texas", "PR"),
]

BENCH = {
    "SPY": {"2026-09-17": -3.9, "2026-09-15": 0.2},
    "XLF": {"2026-09-17": -4.1, "2026-09-15": 0.3},
}

FUNDAMENTALS = {"sector": "Financial Services", "industry": "Insurance", "summary": "賣保險"}


def report():
    return build_report("ACME", {"name": "Acme", "industry": "X"}, FUNDAMENTALS, BENCH, BARS, ARTICLES)


def test_dedupe_ignores_case_and_punctuation():
    assert len(dedupe(ARTICLES)) == 4


def test_only_threshold_days_become_events():
    assert [e["date"] for e in report()["events"]] == ["2026-09-17", "2026-09-15"]


def test_after_close_news_attributes_to_next_day():
    event = next(e for e in report()["events"] if e["date"] == "2026-09-15")
    assert event["sources"] == ["CNBC", "Reuters"]


def test_event_without_news_stays_empty_instead_of_borrowing():
    """9/17 沒有當日新聞時不可以去偷 9/16 的例行報導。"""
    event = next(e for e in report()["events"] if e["date"] == "2026-09-17")
    assert event["articles"] == []


def test_unmatched_articles_split_into_buckets():
    other = report()["other"]
    assert [a["source"] for a in other["analyst"]] == ["Barrons"]
    assert [a["source"] for a in other["routine"]] == ["PR"]


def test_days_before_news_coverage_are_flagged_not_reported_as_no_news():
    """新聞來源靜默截斷時，早於涵蓋範圍的異動日必須標成『沒資料』而不是『沒新聞』。"""
    bars = [{"date": "2026-09-01", "close": 100.0}, {"date": "2026-09-02", "close": 110.0}] + BARS
    r = build_report("ACME", {"name": "Acme", "industry": "X"}, FUNDAMENTALS, BENCH, bars, ARTICLES)
    assert r["news_coverage"] == {"from": "2026-09-15", "to": "2026-09-16"}
    early = next(e for e in r["events"] if e["date"] == "2026-09-02")
    assert early["covered"] is False and early["articles"] == []
    late = next(e for e in r["events"] if e["date"] == "2026-09-17")
    assert late["covered"] is True and late["articles"] == []


def test_every_article_carries_a_sentiment():
    r = report()
    tones = {a["headline"]: a["sentiment"] for e in r["events"] for a in e["articles"]}
    tones.update({a["headline"]: a["sentiment"] for a in r["other"]["analyst"] + r["other"]["routine"]})
    assert tones["ACME wins huge supply deal"] == "positive"
    assert tones["Analyst raises ACME price target to 130"] == "positive"
    assert tones["ACME opens new office in Texas"] == "neutral"


def test_negative_keywords_outweigh_positive():
    from app.analyze import _sentiment
    assert _sentiment("ACME plunges after earnings miss") == "negative"
    assert _sentiment("ACME cuts guidance despite strong demand") == "neutral"


def test_source_counts_cover_every_kept_article():
    r = report()
    assert sum(c for _, c in r["source_counts"]) == r["total_articles"] == 4


def test_sector_maps_to_chinese_label_and_drivers():
    b = report()["basics"]
    assert b["sector_label"] == "金融"
    assert any("利率" in d for d in b["drivers"])


def test_unknown_sector_keeps_original_text_and_has_no_drivers():
    r = build_report("X", {"name": "X", "industry": ""}, {"sector": "Wat"}, BENCH, BARS, ARTICLES)
    assert r["basics"]["sector_label"] == "Wat" and r["basics"]["drivers"] == []


def test_market_context_flags_a_broad_selloff_as_not_company_specific():
    e = next(e for e in report()["events"] if e["date"] == "2026-09-17")
    assert e["market"]["verdict"]["code"] == "market"
    assert [r["symbol"] for r in e["market"]["rows"]] == ["SPY", "XLF"]


def test_market_context_flags_a_lone_mover_as_worth_reading():
    e = next(e for e in report()["events"] if e["date"] == "2026-09-15")
    assert e["market"]["verdict"]["code"] == "alpha"


def test_missing_benchmark_says_unknown_instead_of_guessing():
    r = build_report("ACME", {"name": "A", "industry": ""}, FUNDAMENTALS, {}, BARS, ARTICLES)
    assert r["events"][0]["market"]["verdict"]["code"] == "unknown"


def test_peers_fall_back_to_market_only_when_sector_is_unknown():
    from app.analyze import peers_for
    assert peers_for("Financial Services") == [("SPY", "大盤"), ("XLF", "金融")]
    assert peers_for("Wat") == [("SPY", "大盤")]


def test_verdict_always_states_both_numbers_so_direction_is_recoverable():
    t = verdict_for(-3.0, 0.94, "科技", "TSLA")["text"]
    assert "TSLA -3.00%" in t and "科技 +0.94%" in t


def test_alpha_wording_is_correct_when_the_stock_is_the_lower_one():
    low = verdict_for(2.0, 9.0, "金融")
    high = verdict_for(9.0, 2.0, "金融")
    # 兩個百分比相減是「百分點」。寫成 % 會被讀成「又多漲了 7%」，那是另一個數字
    assert "輸金融 7.0 個百分點" in low["text"]
    assert "贏金融 7.0 個百分點" in high["text"]
    assert low["code"] == high["code"] == "alpha"


def test_a_stock_that_lags_a_rising_sector_is_told_it_was_carried():
    # 漲 2%、板塊漲 9% —— 看起來賺了，其實是板塊抬轎，它自己還扯後腿
    assert "扯後腿" in verdict_for(2.0, 9.0, "金融")["text"]


def test_falling_less_than_the_sector_is_not_called_the_company_s_own_doing():
    # 兩邊都在跌，跌比較少不是「自己的本事」，是抗跌。不准把方向講反
    t = verdict_for(-2.0, -9.0, "金融")["text"]
    assert "抗跌" in t and "本事" not in t


def test_counter_names_which_side_went_up():
    assert "這支 漲、能源 跌" in verdict_for(4.0, -2.0, "能源")["text"]
    assert "這支 跌、能源 漲" in verdict_for(-4.0, 2.0, "能源")["text"]


def test_peer_label_is_not_hardcoded_to_sector():
    # 沒有對應的類股 ETF 時，比較對象是大盤，句子就不該寫「同類股」
    assert "大盤" in verdict_for(5.0, 1.0, "大盤")["text"]
