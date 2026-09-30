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
    ROTATION_NARROW_MAX,
    ROTATION_NOISE_PCT,
    ROTATION_SLOPE_DAYS,
    ROTATION_WINDOWS,
    RRG_BASELINE_DAYS,
    RRG_MOMENTUM_DAYS,
    RRG_ORIGIN_NOISE,
    RRG_TAIL_STRIDE_DAYS,
    SECTOR_ETF,
    SECTOR_LABELS,
)

QUADRANTS = {
    "lead": "領先",
    "weaken": "轉弱",
    "lag": "落後",
    "improve": "改善中",
}


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


def _line(series: list[float], days: int) -> list[float]:
    """這條比值在這段區間走過的路，起點歸零。

    畫成線才看得出「-3.2%」是一路跌下來的，還是先漲一大段再摔回來 —— 這兩件事
    對配置的意義完全不同，但它們的期末數字一模一樣。
    每一點讀作「比區間第一天多幾 %」，跟旁邊那個百分比同一個單位。
    """
    if len(series) <= days:
        return []
    window = series[-days - 1:]
    first = window[0]
    if not first:
        return []
    return [round(v / first * 100 - 100, 2) for v in window]


def _risk(closes: dict[str, list[float]], days: int) -> list[dict]:
    """風險胃納也跟著拉桿走。

    固定看「近三月」的話，拉桿拉到 1 天和拉到半年下半部長得一模一樣 ——
    那條拉桿就變成半殘的，使用者會以為畫面壞了。
    """
    out = []
    for spec in RISK_RATIOS:
        if spec["up"] not in closes or spec["down"] not in closes:
            continue
        series = _ratio(closes, spec["up"], spec["down"])
        move = _change(series, days)
        out.append({
            "name": spec["name"],
            "pair": f"{spec['up']}÷{spec['down']}",
            "line": _line(series, days),
            "move": round(move, 1) if move is not None else None,
            "moves": {label: round(v, 1) for label, n in ROTATION_WINDOWS
                      if (v := _change(series, n)) is not None},
            "tone": _tone(move) if move is not None else "flat",
            "text": spec["on"] if (move or 0) > 0 else spec["off"],
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


def _sma(series: list[float], n: int) -> list[float]:
    """簡單移動平均。回傳長度是 len(series) - n + 1，對齊到每個窗口的最後一天。"""
    total = sum(series[:n])
    out = [total / n]
    for i in range(n, len(series)):
        total += series[i] - series[i - n]
        out.append(total / n)
    return out


def _quadrant(x: float, y: float) -> str:
    """右上領先、右下轉弱、左下落後、左上改善中。原點附近不算數。"""
    if abs(x) < RRG_ORIGIN_NOISE and abs(y) < RRG_ORIGIN_NOISE:
        return "flat"
    if x >= 0:
        return "lead" if y >= 0 else "weaken"
    return "improve" if y >= 0 else "lag"


def _track(closes: dict[str, list[float]], market: list[float], etf: str) -> tuple[list, list]:
    """這個板塊在 RRG 平面上走過的整條路。

    兩個座標都以 0 為中心（不是業界常用的 100）—— 「+4.6」讀作「比大盤強 4.6 個百分點」，
    比「104.6」少一次心算。右上角兩個都是正的，就是最強的角落。
    """
    series = closes.get(etf)
    if not series:
        return [], []
    rs = [a / b * 100 for a, b in zip(series, market) if b]
    if len(rs) < RRG_BASELINE_DAYS + RRG_MOMENTUM_DAYS + 1:
        return [], []
    base = _sma(rs, RRG_BASELINE_DAYS)
    # 強度 = 相對強度離它自己這一季的均線幾 %。用均線當基準，整體牛熊會被一起吃掉
    ratio = [r / b * 100 - 100 for r, b in zip(rs[RRG_BASELINE_DAYS - 1:], base) if b]
    mom = [(ratio[i] + 100) / (ratio[i - RRG_MOMENTUM_DAYS] + 100) * 100 - 100
           for i in range(RRG_MOMENTUM_DAYS, len(ratio))]
    return ratio[RRG_MOMENTUM_DAYS:], mom


def _rrg(closes: dict[str, list[float]], benchmark: str, days: int) -> list[dict]:
    """十一個板塊 = 同一張二維圖上的十一個點，不是十一個維度。

    拉桿決定尾巴涵蓋多長 —— 尾巴的方向就是「錢正在往哪個象限搬」，
    只看當下那一顆點的位置，看不出它是剛進來還是正要走。
    """
    market = closes[benchmark]
    out = []
    for sector, etf in SECTOR_ETF.items():
        ratio, mom = _track(closes, market, etf)
        if not mom:
            continue
        back = min(days, len(mom) - 1)
        # 從最後一天往回數幾天。終點（往回 0 天）永遠在，短區間的點是長區間的子集
        offsets = sorted({0, back} | set(range(0, back + 1, RRG_TAIL_STRIDE_DAYS)), reverse=True)
        out.append({
            "etf": etf,
            "label": SECTOR_LABELS.get(sector, sector),
            "x": round(ratio[-1], 2),
            "y": round(mom[-1], 2),
            "quadrant": _quadrant(ratio[-1], mom[-1]),
            "tail": [{"x": round(ratio[-1 - k], 2), "y": round(mom[-1 - k], 2)} for k in offsets],
        })
    return out


def range_label(days: int) -> str:
    """交易日數翻成人話。滑桿上要讀得懂，所以主單位是「週」，月份只當括號裡的提示。"""
    if days < 5:
        return f"{days} 天"
    months = round(days / 21)
    return f"{days // 5} 週" + (f"（約 {months} 個月）" if months else "")


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

    risk = _risk(closes, days)
    return {
        "asof": asof,
        "days": days,
        "range_label": range_label(days),
        "sectors": sectors,
        "market": {"etf": benchmark,
                   "returns": {label: round(v, 1) for label, n in ROTATION_WINDOWS
                               if (v := _change(market, n)) is not None}},
        "risk": risk,
        "rrg": _rrg(closes, benchmark, days),
        "verdict": _verdict(sectors, risk),
    }
