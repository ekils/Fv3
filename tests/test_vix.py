import pytest

from app.vix import build_vix


def _chart(points, intraday=False, pct=0.0):
    return {"label": "1 個月", "intraday": intraday, "pct": pct,
            "points": [{"t": t, "close": c} for t, c in points]}


def test_daily_alignment_ignores_the_exchange_timezone():
    # VIX 掛芝加哥（-05:00）、股價掛紐約（-04:00）。拿時間字串比大小，
    # 每一筆都會配到 VIX 的「前一天」，相關係數會被這一天的位移抹平。
    stock = _chart([(f"2026-08-{d:02d}T00:00:00-04:00", 100.0 + d) for d in (3, 4, 5)])
    vix = _chart([(f"2026-08-{d:02d}T00:00:00-05:00", 20.0 - d) for d in (3, 4, 5)])
    rows = build_vix(stock, vix)["points"]
    assert [r["vix"] for r in rows] == [17.0, 16.0, 15.0]


def test_opposite_moves_give_negative_correlation():
    stock = _chart([(f"2026-08-{d:02d}T00:00:00-04:00", c)
                    for d, c in ((3, 100.0), (4, 110.0), (5, 99.0), (6, 120.0))])
    vix = _chart([(f"2026-08-{d:02d}T00:00:00-05:00", c)
                  for d, c in ((3, 20.0), (4, 18.0), (5, 22.0), (6, 16.0))])
    assert build_vix(stock, vix)["corr"] == -1.0


def test_vix_range_is_taken_from_the_aligned_rows_not_the_raw_feed():
    # 多出來的那天沒有對應的股價，不該讓區間看起來更寬
    stock = _chart([(f"2026-08-{d:02d}T00:00:00-04:00", 100.0) for d in (4, 5, 6)])
    vix = _chart([(f"2026-08-{d:02d}T00:00:00-05:00", c)
                  for d, c in ((4, 15.0), (5, 16.0), (6, 17.0), (7, 90.0))])
    out = build_vix(stock, vix)
    assert (out["vix_low"], out["vix_high"], out["vix_now"]) == (15.0, 17.0, 17.0)


def test_too_few_aligned_points_raises_instead_of_drawing_a_lie():
    stock = _chart([("2026-08-05T00:00:00-04:00", 100.0)])
    vix = _chart([("2026-08-05T00:00:00-05:00", 15.0)])
    with pytest.raises(RuntimeError):
        build_vix(stock, vix)
