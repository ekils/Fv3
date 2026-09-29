from app.config import FRED_EFFECT, FRED_EFFECT_BY_SECTOR, FRED_SERIES, SECTOR_FRED
from app.macro import build_macro, series_for


def obs(pairs):
    return [{"date": d, "value": v} for d, v in pairs]


def test_sector_picks_its_own_indicators_and_unknown_falls_back():
    assert series_for("Real Estate")[0] == "MORTGAGE30US"
    assert series_for("") == series_for("不存在的產業")


def test_level_series_reports_latest_and_half_year_change():
    raw = {"FEDFUNDS": obs([("2026-01-01", 5.0), ("2026-03-01", 4.5), ("2026-09-01", 4.25)])}
    row = build_macro("", {**raw, "CPIAUCSL": [], "UNRATE": []})["rows"][0]
    assert row["value"] == 4.25
    assert row["change"] == -0.25  # 2026-09-01 往回 182 天落在 2026-03-01 那筆


def test_index_series_becomes_year_over_year_percent():
    raw = {"CPIAUCSL": obs([("2025-09-01", 100.0), ("2026-09-01", 103.0)])}
    row = build_macro("", {"FEDFUNDS": [], **raw, "UNRATE": []})["rows"][0]
    assert row["name"] == "CPI 通膨年增率"
    assert row["value"] == 3.0


def test_missing_history_reports_no_change_instead_of_zero():
    raw = {"UNRATE": obs([("2026-09-01", 4.1)])}
    row = build_macro("", {"FEDFUNDS": [], "CPIAUCSL": [], **raw})["rows"][0]
    assert row["change"] is None


def test_empty_series_is_dropped_not_rendered_as_blank():
    assert build_macro("", {"FEDFUNDS": [], "CPIAUCSL": [], "UNRATE": []})["rows"] == []


def test_every_configured_indicator_has_a_direction_rule():
    """SECTOR_FRED 裡加了指標、方向表卻忘了加，畫面會直接爆掉 —— 這裡讓它先爆。"""
    for sid in FRED_SERIES:
        sign, up, down = FRED_EFFECT[sid]
        assert sign in (1, -1)
        assert up and down and up != down
    for sector, overrides in FRED_EFFECT_BY_SECTOR.items():
        assert sector in SECTOR_FRED
        for sid in overrides:
            assert sid in FRED_SERIES


def test_the_same_indicator_can_be_good_for_one_sector_and_bad_for_another():
    # 升息壓垮多數公司，但銀行保險靠利差賺錢 —— 同一個數字，兩個相反的結論
    raw = {"FEDFUNDS": obs([("2026-01-01", 3.0), ("2026-09-01", 4.0)]),
           "CPIAUCSL": [], "UNRATE": [], "DGS10": [], "DRCCLACBS": [], "INDPRO": []}
    assert build_macro("Financial Services", raw)["rows"][0]["effect"]["good"] is True
    assert build_macro("Technology", raw)["rows"][0]["effect"]["good"] is False


def test_an_indicator_that_did_not_move_gets_no_verdict():
    # 沒動就不給方向，不准硬湊一個好壞出來
    raw = {"UNRATE": obs([("2026-01-01", 4.1), ("2026-09-01", 4.1)]),
           "FEDFUNDS": [], "CPIAUCSL": []}
    assert build_macro("", raw)["rows"][0]["effect"] is None


def test_a_falling_indicator_gets_the_falling_sentence():
    raw = {"UNRATE": obs([("2026-01-01", 4.5), ("2026-09-01", 4.1)]),
           "FEDFUNDS": [], "CPIAUCSL": []}
    row = build_macro("", raw)["rows"][0]
    assert row["effect"]["good"] is True
    assert row["effect"]["text"] == FRED_EFFECT["UNRATE"][2]
