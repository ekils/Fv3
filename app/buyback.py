"""公司回購：花了多少錢買回自己的股票，以及那筆錢有沒有真的變成你的。

回購金額本身是個很容易騙人的數字。一家公司可以一年花 200 億買回股票，同時發出
價值 190 億的股票給員工 —— 帳上寫著「回饋股東 200 億」，實際上股本幾乎沒縮。

唯一騙不了人的是流通股數。股數真的變少，你手上每一股才真的代表更多公司。
所以這裡把兩件事並排放：**花了多少** 和 **股數少了多少**。
"""

from datetime import date

from .config import BUYBACK_REAL_SHRINK


def _annual(shares: list[dict]) -> float:
    """股數的年化變化率（%）。負數代表股本在縮。"""
    first, last = shares[0], shares[-1]
    years = (date.fromisoformat(last["period"]) - date.fromisoformat(first["period"])).days / 365.25
    if years < 0.5 or not first["count"]:
        raise RuntimeError("股數資料不足一年，看不出趨勢")
    return ((last["count"] / first["count"]) ** (1 / years) - 1) * 100


def _boost(shrink: float) -> float:
    """股數少了，每股盈餘被推高幾 %。

    披薩切成 10 片、買回 2 片丟掉，剩下 8 片 —— 公司沒有多賺，但你那一片變大了。
    這是回購能幫到股價的**唯一**機制，所以「股數有沒有真的變少」不是細節，是全部。
    """
    return (1 / (1 + shrink / 100) - 1) * 100


def _verdict(yield_pct: float, shrink: float) -> dict:
    """花錢 vs 縮股本，兩者對不上的時候要講出來。"""
    if shrink <= -BUYBACK_REAL_SHRINK:
        return {"code": "real", "text":
                f"這筆錢每年把每股盈餘推高 {_boost(shrink):.1f}% —— "
                f"公司沒有多賺，但股數年減 {abs(shrink):.1f}%，你手上每一股代表的公司變多了。"
                f"其他條件不變，股價也該跟著高這麼多。"}
    if yield_pct >= 1.0:
        return {"code": "offset", "text":
                "錢花了，但股數幾乎沒少 —— 大多拿去抵銷發給員工的股票。"
                "對每股盈餘的推力接近 0，這筆錢沒有變成你的。"}
    return {"code": "none", "text": "沒什麼在回購，這條路上沒有推力。"}


def _dividend_yield(dividends: list[dict], price: float) -> float | None:
    """前瞻配息殖利率：最近一筆配息 × 一年配幾次 ÷ 股價。

    不用「過去一年加總」，是因為 Yahoo 的配息序列偶爾缺一筆，加總會直接少報一整季。
    間隔的中位數推得出一年配幾次，而且剛調升配息的公司也能立刻反映。
    """
    if len(dividends) < 2 or not price:
        return None
    days = sorted((date.fromisoformat(b["date"]) - date.fromisoformat(a["date"])).days
                  for a, b in zip(dividends, dividends[1:]))
    gap = days[len(days) // 2]
    if gap < 20:
        return None
    return dividends[-1]["amount"] * round(365 / gap) / price * 100


def _annual_rows(annual: list[dict]) -> list[dict]:
    """每年花了多少，以及那一年年底股數比前一年少多少。

    兩欄一定要並排。花的錢加倍、股數卻沒有跟著多減一倍，就是那筆錢有一半沒變成你的。
    """
    rows = []
    for i, r in enumerate(annual):
        prev = annual[i - 1]["shares"] if i and annual[i - 1]["shares"] else 0
        change = (r["shares"] / prev - 1) * 100 if prev and r["shares"] else None
        rows.append({
            "year": r["year"],
            "amount": round(r["amount"]),
            "shares": round(r["shares"]),
            "change": round(change, 1) if change is not None else None,
        })
    return rows


def build_buyback(raw: dict, shares_now: float) -> dict:
    spend = raw["spend"]
    if len(spend) < 4:
        raise RuntimeError("回購資料不足四季，算不出年度金額")
    shares = raw["shares"]
    if len(shares) < 2:
        raise RuntimeError("股數資料不足兩季")

    year = sum(r["amount"] for r in spend[:4])
    cap = shares_now * raw["price"]
    shrink = _annual(shares)
    buyback_yield = round(year / cap * 100, 2) if cap else 0.0
    div = _dividend_yield(raw.get("dividends", []), raw["price"])

    return {
        "year": round(year),
        "yield": buyback_yield,
        "boost": round(_boost(shrink), 1),
        "dividend_yield": round(div, 2) if div is not None else None,
        "total_yield": round(buyback_yield + (div or 0), 2),
        "market_cap": round(cap),
        "shares_from": {"period": shares[0]["period"], "count": round(shares[0]["count"])},
        "shares_to": {"period": shares[-1]["period"], "count": round(shares[-1]["count"])},
        "shrink": round(shrink, 2),
        "verdict": _verdict(year / cap * 100 if cap else 0.0, shrink),
        "annual": _annual_rows(raw.get("annual", [])),
        "shares": [{"period": s["period"], "count": round(s["count"])} for s in shares],
    }
