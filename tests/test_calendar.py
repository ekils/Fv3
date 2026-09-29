from app.calendar import build_calendar


def names(year, month, sector="", earnings=()):
    c = build_calendar(year, month, sector, list(earnings))
    return [(e["date"], e["name"], e["impact"]) for e in c["events"]]


def test_first_and_third_friday_are_computed_not_guessed():
    got = dict((n, d) for d, n, _ in names(2026, 10))
    assert got["非農就業報告"] == "2026-10-02"
    assert got["選擇權到期日"] == "2026-10-16"


def test_every_wednesday_gets_an_eia_report():
    eia = [d for d, n, _ in names(2026, 10) if n.startswith("EIA")]
    assert eia == ["2026-10-07", "2026-10-14", "2026-10-21", "2026-10-28"]


def test_sector_scoped_event_is_direct_only_for_that_sector():
    assert ("2026-10-07", "EIA 原油庫存週報", "direct") in names(2026, 10, "Energy")
    assert ("2026-10-07", "EIA 原油庫存週報", "unrelated") in names(2026, 10, "Technology")


def test_earnings_outside_the_month_are_dropped():
    e = [{"date": "2026-11-20", "symbol": "TSLA", "hour": "amc"}]
    assert not [x for x in names(2026, 10, "", e) if "財報" in x[1]]


def test_fomc_absence_is_reported_not_faked():
    assert build_calendar(2026, 10, "", [])["has_fomc_data"] is False
