"""「現在的股價，落在自己的軌道上哪裡」。

每一次財報公布把股價切成一段。每一段用當時的股價重心當起點、畫一條斜線當軌道，
再用這支股票自己平常的晃動幅度往上下撐開兩層帶子。掉到帶子下緣就是相對便宜。

這裡沒有預測，只有回歸線和標準差。軌道往哪走完全由已經發生的股價和盈餘決定。
"""

from datetime import date, timedelta
from statistics import median, stdev

from .config import (
    TREND_AFTER_DAYS,
    TREND_BEFORE_DAYS,
    TREND_EPS_WEIGHT,
    TREND_NEXT_DAYS,
    TREND_SIGMA_QUARTERS,
)


def _days(a: str, b: str) -> int:
    return (date.fromisoformat(b) - date.fromisoformat(a)).days


def _shift(day: str, n: int) -> str:
    return (date.fromisoformat(day) + timedelta(days=n)).isoformat()


def _around(closes: list[dict], day: str) -> list[dict]:
    """財報公布日前後那一段收盤價。這段就是市場對這次財報的反應。"""
    lo, hi = _shift(day, -TREND_BEFORE_DAYS), _shift(day, TREND_AFTER_DAYS)
    return [c for c in closes if lo <= c["date"] <= hi]


def _halves(closes: list[dict], day: str) -> tuple[list[float], list[float]]:
    """同一段拆成公布前與公布後。前後分開才算得出「消息前後差多少」。"""
    rows = _around(closes, day)
    return ([c["close"] for c in rows if c["date"] < day],
            [c["close"] for c in rows if c["date"] >= day])


def _sigma(means: list[float]) -> float:
    """這支股票平常一季會晃多大。相鄰兩個窗口均價的差距，再取標準差。"""
    diffs = [abs(means[i] - means[i + 1]) for i in range(len(means) - 1)]
    return stdev(diffs)


def _price_slope(rows: list[dict]) -> float:
    """這一段股價的一階回歸斜率，單位是「元／天」。

    刻意用真實天數當 x 而不是資料筆數：後面要拿這個斜率乘上日曆天數推軌道，
    兩邊單位不一致的話線會整條歪掉。
    """
    x = [_days(rows[0]["date"], r["date"]) for r in rows]
    y = [r["close"] for r in rows]
    mx, my = sum(x) / len(x), sum(y) / len(y)
    var = sum((v - mx) ** 2 for v in x)
    return sum((a - mx) * (b - my) for a, b in zip(x, y)) / var


def _eps_drift(known: list[tuple[str, float]], i: int, anchor: float) -> float:
    """盈餘成長換算成「元／天」：本益比不變的話，這個成長率一年該把股價推多少。

    直接拿成長率（沒有單位）去跟股價斜率（元／天）相加是錯的，要先換算。
    """
    growth = known[i][1] / known[i - 4][1] - 1
    return anchor * growth / 365.25


def _next_release(releases: list[str], at: str) -> str:
    """下一次財報公布日。Yahoo 連還沒發生的那一筆也會給，有排定日期就用真的。"""
    later = [d for d in releases if d > at]
    return later[0] if later else _shift(at, TREND_NEXT_DAYS)


def _fit_rows(closes: list[dict], start: str, end: str) -> list[dict]:
    """回歸要吃的那段股價。

    最後一段要畫到下一次財報公布日，那幾天還沒發生。剛公布完財報時段内只有幾天
    資料卻要畫一整季，斜率會誇張到離譜（TSLA 出現過一季跌到剩五分之一）。

    往前借天數把樣本補到「至少蓋住它要畫的一半」為止就好。借滿全長的話會把上一季
    財報前的走勢混進來 —— 那正是分段要隔開的東西。
    """
    rows = [c for c in closes if start <= c["date"] <= end]
    short = _days(start, end) // 2 - _days(start, rows[-1]["date"]) if rows else 0
    if short <= 0:
        return rows
    return [c for c in closes if _shift(start, -short) <= c["date"] <= end]


def _segment(closes: list[dict], known: list[tuple[str, float]], i: int,
             start: str, end: str, sigma: float) -> dict | None:
    """軌道的高度來自財報前後的股價重心，方向來自整段期間的回歸。

    方向刻意不用財報那四週算 —— 四週的斜率推到整季會誇張到離譜（TSLA 推出過
    一季跌到剩五分之一）。斜率涵蓋的天數必須等於它要畫過的天數。
    """
    around = _around(closes, known[i][0])
    rows = _fit_rows(closes, start, end)
    if len(around) < 2 or len({c["date"] for c in rows}) < 2:
        return None

    anchor = median(c["close"] for c in around)
    slope = (_price_slope(rows) + TREND_EPS_WEIGHT * _eps_drift(known, i, anchor)) / (1 + TREND_EPS_WEIGHT)
    return {
        "from": start,
        "to": end,
        "at": known[i][0],
        "y0": round(anchor + slope * _days(known[i][0], start), 3),
        "y1": round(anchor + slope * _days(known[i][0], end), 3),
        "sigma": round(sigma, 3),
    }


def _verdict(price: float, fair: float, sigma: float) -> dict:
    gap = price - fair
    if gap < -sigma:
        return {"code": "cheap", "text": "跌破軌道下緣 —— 比這支自己平常的位置低"}
    if gap > sigma:
        return {"code": "rich", "text": "衝出軌道上緣 —— 比這支自己平常的位置高"}
    return {"code": "fair", "text": "在軌道內 —— 就是這支平常該在的位置"}


def build_trend(fin: dict, known: list[tuple[str, float]], years: int) -> dict:
    """years 跟本益比共用同一根拉桿，兩張圖講的是同一段時間。"""
    closes = fin["closes"]
    if len(closes) < 2:
        raise RuntimeError("價格資料不足，畫不出軌道")

    start = _shift(closes[-1]["date"], -round(365.25 * years))
    window = [c for c in closes if c["date"] >= start] or closes
    usable = [i for i in range(4, len(known)) if window[0]["date"] <= known[i][0] <= window[-1]["date"]]
    if not usable:
        raise RuntimeError("這段期間內沒有財報公布日，切不出軌道")

    sigmas = _sigmas(closes, known, usable)
    # 最後一段畫到下一次財報公布日 —— 這張圖要回答的是「接下來這一季的合理區間」，
    # 停在今天就等於沒有區間可以對照。
    ends = [known[i][0] for i in usable[1:]] + [_next_release(fin["releases"], known[usable[-1]][0])]
    starts = [window[0]["date"]] + [known[i][0] for i in usable[1:]]
    segs = [s for i, a, b in zip(usable, starts, ends)
            if (s := _segment(closes, known, i, a, b, sigmas[i]))]
    if not segs:
        raise RuntimeError("財報前後抓不到足夠的股價，切不出軌道")

    last, price = segs[-1], window[-1]["close"]
    fair = last["y0"] + (last["y1"] - last["y0"]) * _days(last["from"], window[-1]["date"]) / max(
        _days(last["from"], last["to"]), 1)
    return {
        "years": years,
        "today": window[-1]["date"],
        # 每一個交易日都畫出來。抽樣畫縮圖的話，換一次回看年數就換一批被抽中的日子，
        # 看起來像股價變了 —— 但股價是事實，不該隨著拉桿改變。
        "points": [{"d": c["date"], "c": round(c["close"], 2)} for c in window],
        "segments": segs,
        "now": {
            "price": round(price, 2),
            "fair": round(fair, 2),
            "sigma": last["sigma"],
            "gap": round((price / fair - 1) * 100, 1),
            "verdict": _verdict(price, fair, last["sigma"]),
        },
        "eps_weight": TREND_EPS_WEIGHT,
    }


def _sigmas(closes: list[dict], known: list[tuple[str, float]], usable: list[int]) -> dict[int, float]:
    """每一段的帶寬。用「到那一季為止」最近幾季算，軌道才不會用到未來的資訊。"""
    out = {}
    for i in usable:
        means = []
        for j in range(max(0, i - TREND_SIGMA_QUARTERS + 1), i + 1):
            before, after = _halves(closes, known[j][0])
            means += [sum(x) / len(x) for x in (before, after) if x]
        out[i] = _sigma(means) if len(means) > 2 else 0.0
    return out
