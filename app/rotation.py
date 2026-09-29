"""資金流向：錢正在從哪個板塊搬到哪個板塊。

市場的錢是一袋。這邊風險高就搬去另一邊 —— 這個模型對，但直接看「哪個板塊漲最多」
會被騙，因為那袋錢本身會變大變小（新資金進場、聯準會放水），整袋變大的時候大家都漲。

要看的是**相對強度**：板塊價格 ÷ 大盤價格。整袋錢變大變小會被分子分母一起吃掉，
剩下的才是搬家。

另外算三個風險胃納比值。它們互相打架的時候不要去調和 —— 那個矛盾本身就是訊息：
錢不是在「進攻 vs 防禦」之間輪動，而是全部從各處抽出來灌進同一個地方。
"""

from .config import (
    RISK_RATIOS,
    ROTATION_RANGES,
    ROTATION_NARROW_MAX,
    ROTATION_NOISE_PCT,
    ROTATION_SLOPE_DAYS,
    ROTATION_WINDOWS,
    SECTOR_ETF,
    SECTOR_LABELS,
)


def _change(series: list[float], days: int) -> float | None:
    """這幾個交易日以來變動幾 %。資料不夠長就回 None，不拿最舊那筆硬湊。"""
    if len(series) <= days or not series[-days - 1]:
        return None
    return (series[-1] / series[-days - 1] - 1) * 100


def _tone(value: float) -> str:
    if value > ROTATION_NOISE_PCT:
        return "in"
    if value < -ROTATION_NOISE_PCT:
        return "out"
    return "flat"


def _ratio(closes: dict[str, list[float]], up: str, down: str) -> list[float]:
    a, b = closes[up], closes[down]
    return [x / y for x, y in zip(a, b) if y]


def _risk(closes: dict[str, list[float]]) -> list[dict]:
    out = []
    for spec in RISK_RATIOS:
        if spec["up"] not in closes or spec["down"] not in closes:
            continue
        series = _ratio(closes, spec["up"], spec["down"])
        moves = {label: _change(series, n) for label, n in ROTATION_WINDOWS}
        recent = moves["3月"]
        out.append({
            "name": spec["name"],
            "pair": f"{spec['up']}÷{spec['down']}",
            "moves": {k: round(v, 1) for k, v in moves.items() if v is not None},
            "tone": _tone(recent) if recent is not None else "flat",
            "text": spec["on"] if (recent or 0) > 0 else spec["off"],
        })
    return out


def _verdict(sectors: list[dict], risk: list[dict]) -> dict:
    """幾個板塊在贏大盤，決定「輪動」這個概念現在還成不成立。"""
    winners = [s for s in sectors if s["rs"] > ROTATION_NOISE_PCT]
    if len(winners) > ROTATION_NARROW_MAX:
        return {"code": "broad", "text":
                f"{len(winners)} 個板塊在贏大盤 —— 錢分散在多處，板塊輪動的邏輯成立。"}
    names = "、".join(s["label"] for s in winners) or "沒有任何板塊"
    fighting = len({r["tone"] for r in risk if r["tone"] != "flat"}) > 1
    tail = ("而且風險胃納的比值互相打架 —— 這不是「進攻 vs 防禦」在輪動，"
            "是錢從每一個別的地方抽出來，全部灌進同一個地方。") if fighting else ""
    return {"code": "narrow", "text":
            f"只有 {names} 在贏大盤，其他全在失血。{tail}"}


def build_rotation(closes: dict[str, list[float]], asof: str, benchmark: str,
                   days: int = ROTATION_SLOPE_DAYS) -> dict:
    market = closes.get(benchmark)
    if not market or len(market) <= days:
        raise RuntimeError("大盤資料不足，算不出相對強度")

    sectors = []
    for sector, etf in SECTOR_ETF.items():
        series = closes.get(etf)
        if not series:
            continue
        strength = [x / y for x, y in zip(series, market) if y]
        rs = _change(strength, days)
        if rs is None:
            continue
        sectors.append({
            "etf": etf,
            "label": SECTOR_LABELS.get(sector, sector),
            "rs": round(rs, 2),
            "tone": _tone(rs),
            "returns": {label: round(v, 1) for label, n in ROTATION_WINDOWS
                        if (v := _change(series, n)) is not None},
        })
    if not sectors:
        raise RuntimeError("沒有任何類股 ETF 的資料")

    risk = _risk(closes)
    return {
        "asof": asof,
        "days": days,
        "range_label": dict((n, l) for l, n in ROTATION_RANGES).get(days, f"{days} 天"),
        "ranges": [{"label": l, "days": n} for l, n in ROTATION_RANGES],
        "sectors": sectors,
        "market": {"etf": benchmark,
                   "returns": {label: round(v, 1) for label, n in ROTATION_WINDOWS
                               if (v := _change(market, n)) is not None}},
        "risk": risk,
        "verdict": _verdict(sectors, risk),
    }
