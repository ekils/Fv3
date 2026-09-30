"""把新聞和價格壓縮成「值得看的日子」。其餘歸類摺疊。"""

import re
from collections import Counter
from datetime import datetime, timedelta

from .config import (
    ANALYST_KEYWORDS,
    MARKET_CLOSE_UTC_HOUR,
    MOVE_THRESHOLD_PCT,
    NEGATIVE_KEYWORDS,
    POSITIVE_KEYWORDS,
    ALPHA_NOISE_PCT,
    BENCHMARK_SYMBOL,
    SECTOR_DRIVERS,
    SECTOR_ETF,
    SECTOR_LABELS,
)


def _key(headline: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", headline.lower())[:80]


def dedupe(articles: list[dict]) -> list[dict]:
    seen: dict[str, dict] = {}
    for a in articles:
        seen.setdefault(_key(a["headline"]), a)
    return sorted(seen.values(), key=lambda a: a["ts"], reverse=True)


def _with_pct(bars: list[dict]) -> list[dict]:
    out = [{**bars[0], "pct": 0.0}]
    for prev, cur in zip(bars, bars[1:]):
        pct = (cur["close"] - prev["close"]) / prev["close"] * 100
        out.append({**cur, "pct": round(pct, 2)})
    return out


def _trading_day(article: dict) -> str:
    """一篇新聞能影響哪一個交易日：收盤後發布的算隔天。"""
    ts = datetime.fromisoformat(article["ts"])
    day = ts.date()
    if ts.hour >= MARKET_CLOSE_UTC_HOUR:
        day += timedelta(days=1)
    return day.isoformat()


def _hits(text: str, keywords: tuple[str, ...]) -> int:
    return sum(1 for k in keywords if re.search(rf"\b{re.escape(k)}\b", text))


def _sentiment(headline: str) -> str:
    text = headline.lower()
    pos, neg = _hits(text, POSITIVE_KEYWORDS), _hits(text, NEGATIVE_KEYWORDS)
    if pos > neg:
        return "positive"
    if neg > pos:
        return "negative"
    return "neutral"


def _is_analyst(article: dict) -> bool:
    text = f"{article['headline']} {article['summary']}".lower()
    return any(k in text for k in ANALYST_KEYWORDS)


def _basics(fundamentals: dict) -> dict:
    sector = fundamentals.get("sector", "")
    return {
        **fundamentals,
        "sector_label": SECTOR_LABELS.get(sector, sector),
        "drivers": list(SECTOR_DRIVERS.get(sector, ())),
    }


def _market(day: str, pct: float, sector: str, bench: dict) -> dict:
    """同一天大盤和同產業 ETF 動了多少，以及「這是不是這家公司自己的事」。"""
    rows = [{"symbol": s, "label": l, "pct": bench.get(s, {}).get(day)}
            for s, l in peers_for(sector)]
    rows = [r for r in rows if r["pct"] is not None]
    last = rows[-1] if rows else None
    return {
        "rows": rows,
        "verdict": verdict_for(pct, last["pct"], last["label"]) if last else verdict_for(pct, None, ""),
    }


def verdict_for(pct: float, peer: float | None, peer_label: str, symbol: str = "這支") -> dict:
    """比較對象是誰、誰高誰低、差多少，三件事都要寫進句子裡。

    只講對方的數字（舊版的寫法）讀者無法還原方向 —— 而且「只有」這種詞會在
    這支比對方低的時候整句話變成謊話。
    """
    if peer is None:
        return {"code": "unknown", "text": "拿不到同期的大盤／類股資料，無法判斷是不是個別事件。"}

    gap = pct - peer
    side = "贏" if gap > 0 else "輸"
    versus = f"{symbol} {pct:+.2f}%，{peer_label} {peer:+.2f}%"
    # 兩個百分比相減是「百分點」，不是「%」。寫成 % 會被讀成「又多漲了 8.7%」
    margin = f"{side}{peer_label} {abs(gap):.1f} 個百分點"

    if pct * peer < 0:
        return {"code": "counter", "text":
                f"逆勢：{versus} —— 兩邊方向相反，{symbol} {'漲' if pct > 0 else '跌'}、"
                f"{peer_label} {'跌' if pct > 0 else '漲'}，{margin}，最值得看新聞。"}
    if abs(gap) < ALPHA_NOISE_PCT:
        return {"code": "market", "text":
                f"跟著{peer_label}走：{versus}，只差 {abs(gap):.1f} 個百分點 —— "
                f"這是整個{peer_label}的事，不是這家公司自己的本事。"}
    if gap > 0:
        tail = "這一段是公司自己的本事，不是整個板塊在漲" if peer > 0 else "整片在跌，它比較抗跌"
    else:
        tail = f"錢是進了整個{peer_label}，這一支反而扯後腿" if peer > 0 else f"跌得比{peer_label}還兇"
    return {"code": "alpha", "text": f"個股獨走：{versus} —— {margin}，{tail}，新聞值得看。"}


def peers_for(sector: str) -> list[tuple[str, str]]:
    etf = SECTOR_ETF.get(sector, "")
    peers = [(BENCHMARK_SYMBOL, "大盤")]
    if etf:
        peers.append((etf, SECTOR_LABELS.get(sector, sector)))
    return peers


def build_report(
    symbol: str,
    profile: dict,
    fundamentals: dict,
    bench: dict,
    bars: list[dict],
    articles: list[dict],
) -> dict:
    prices = _with_pct(bars)
    articles = [{**a, "sentiment": _sentiment(a["headline"])} for a in dedupe(articles)]

    by_day: dict[str, list[dict]] = {}
    for a in articles:
        by_day.setdefault(_trading_day(a), []).append(a)

    events = []
    matched: set[str] = set()
    for bar in prices[1:]:
        if abs(bar["pct"]) < MOVE_THRESHOLD_PCT:
            continue
        hits = by_day.get(bar["date"], [])
        matched.update(_key(a["headline"]) for a in hits)
        events.append(
            {
                "date": bar["date"],
                "close": bar["close"],
                "pct": bar["pct"],
                "articles": hits,
                "sources": sorted({a["source"] for a in hits}),
                "market": _market(bar["date"], bar["pct"], fundamentals.get("sector", ""), bench),
            }
        )

    rest = [a for a in articles if _key(a["headline"]) not in matched]
    return {
        "symbol": symbol,
        "name": profile["name"],
        "industry": profile["industry"],
        "logo": profile.get("logo", ""),
        "basics": _basics(fundamentals),
        "threshold": MOVE_THRESHOLD_PCT,
        "prices": prices,
        "events": sorted(events, key=lambda e: e["date"], reverse=True),
        "other": {
            "analyst": [a for a in rest if _is_analyst(a)],
            "routine": [a for a in rest if not _is_analyst(a)],
        },
        "source_counts": Counter(a["source"] for a in articles).most_common(),
        "total_articles": len(articles),
    }
