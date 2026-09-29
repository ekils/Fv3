"""把 VIX（市場恐慌指數）和個股股價放在同一張圖上。

VIX 是 S&P 500 選擇權隱含的未來 30 天波動度，市場在付多少錢買保險。
這裡唯一算出來的判斷是「兩邊每天的漲跌方向有多同步」，一個相關係數，沒有預測。
"""

from datetime import date, datetime

from .series import at_or_before


def _when(t: str, intraday: bool):
    """VIX 掛在芝加哥時區、股價掛在紐約時區，差一小時。

    日線只有「哪一天」有意義，所以比日期；分線才比真正的時刻（帶時區的
    datetime 自己會換算，直接比字串會把整條線位移一天）。
    """
    return datetime.fromisoformat(t) if intraday else date.fromisoformat(t[:10])


def _changes(closes: list[float]) -> list[float]:
    return [(b - a) / a * 100 for a, b in zip(closes, closes[1:]) if a]


def _corr(xs: list[float], ys: list[float]) -> float:
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sx = sum((x - mx) ** 2 for x in xs) ** 0.5
    sy = sum((y - my) ** 2 for y in ys) ** 0.5
    return cov / (sx * sy) if sx and sy else 0.0


def build_vix(stock: dict, vix: dict) -> dict:
    """以個股的時間軸為準，VIX 取「當下或之前最近的一筆」。

    兩邊的交易時間不會完全一致，直接用位置對齊會讓兩條線錯開好幾天。
    """
    intraday = stock["intraday"]
    fear = [{"k": _when(p["t"], intraday), "close": p["close"]} for p in vix["points"]]
    rows = [{"t": p["t"], "close": p["close"], "vix": v["close"]}
            for p in stock["points"]
            if (v := at_or_before(fear, "k", _when(p["t"], intraday)))]
    if len(rows) < 3:
        raise RuntimeError("這個區間對得起來的資料點太少，畫不出對照")

    changes = _changes([r["close"] for r in rows]), _changes([r["vix"] for r in rows])
    vixes = [r["vix"] for r in rows]
    return {
        "label": stock["label"],
        "intraday": intraday,
        "points": rows,
        "pct": stock["pct"],
        "vix_now": vixes[-1],
        "vix_low": min(vixes),
        "vix_high": max(vixes),
        "corr": round(_corr(*changes), 2),
        "days": len(rows),
    }
