"""資料來源。每個 fetch_* 成功回傳資料，失敗直接 raise — 不做靜默回退。"""

import asyncio
import os
from datetime import date, datetime, timedelta, timezone

import httpx
import yfinance as yf

from .config import (
    ATTENTION_DAYS,
    BENCHMARK_SYMBOL,
    BUYBACK_ROW,
    BUYBACK_YEARS,
    CHART_RANGES,
    DIVIDEND_SAMPLE,
    EARNINGS_DATES_LIMIT,
    EPS_SURPRISE_QUARTERS,
    FRED_HISTORY_DAYS,
    HTTP_RETRY_ATTEMPTS,
    HTTP_RETRY_BACKOFF_SECONDS,
    HTTP_TIMEOUT_SECONDS,
    RISK_EXTRA_SYMBOLS,
    ROTATION_HISTORY_DAYS,
    SECTOR_ETF,
    VALUATION_YEARS,
)

FINNHUB_BASE = "https://finnhub.io/api/v1"
FRED_BASE = "https://api.stlouisfed.org/fred"


async def _get(client: httpx.AsyncClient, url: str, params: dict) -> httpx.Response:
    """GET，遇到 5xx 就再問一次。

    上游回 502 代表「我這一秒接不到」，不代表資料有問題 —— 第一次就放棄的話，
    整張卡會因為對方打了個嗝而變成一行錯誤訊息。4xx 不重試：那是我們問錯了。
    """
    for attempt in range(HTTP_RETRY_ATTEMPTS):
        r = await client.get(url, params=params)
        if r.status_code < 500 or attempt == HTTP_RETRY_ATTEMPTS - 1:
            r.raise_for_status()
            return r
        await asyncio.sleep(HTTP_RETRY_BACKOFF_SECONDS * (attempt + 1))


def _finnhub_token() -> str:
    token = os.environ.get("FINNHUB_API_KEY", "").strip()
    if not token:
        raise RuntimeError("FINNHUB_API_KEY 未設定，請複製 .env.example 成 .env 並填入")
    return token


def _fred_token() -> str:
    token = os.environ.get("FRED_API_KEY", "").strip()
    if not token:
        raise RuntimeError("FRED_API_KEY 未設定，請到 fredaccount.stlouisfed.org/apikeys 申請後填入 .env")
    return token


async def fetch_fred(series_ids: tuple[str, ...]) -> dict[str, list[dict]]:
    """每個指標的歷史觀測值，由舊到新。一次把這個產業要的全部平行抓回來。"""
    start = (date.today() - timedelta(days=FRED_HISTORY_DAYS)).isoformat()

    async def one(client: httpx.AsyncClient, sid: str) -> list[dict]:
        r = await _get(client, f"{FRED_BASE}/series/observations",
                       {"series_id": sid, "api_key": _fred_token(), "file_type": "json",
                        "observation_start": start})
        return [{"date": o["date"], "value": float(o["value"])}
                for o in r.json()["observations"] if o["value"] != "."]

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as client:
        series = await asyncio.gather(*(one(client, s) for s in series_ids))
    return dict(zip(series_ids, series))


async def _finnhub_news_by_day(symbol: str, days: list[date]) -> list[list[dict]]:
    """一天一個請求。

    company-news 一次最多回大約 250 則，而且是從最新的那天往回填 —— 問一整個區間
    的話，熱門股光最近兩三天就把額度吃光，區間前半段會靜默變成「那天沒新聞」。
    """
    async def one(client: httpx.AsyncClient, d: date) -> list[dict]:
        r = await _get(client, f"{FINNHUB_BASE}/company-news",
                       {"symbol": symbol, "from": d.isoformat(), "to": d.isoformat(),
                        "token": _finnhub_token()})
        return r.json()

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as client:
        return await asyncio.gather(*(one(client, d) for d in days))


def _span(start: date, end: date) -> list[date]:
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


async def fetch_news_counts(symbol: str) -> list[dict]:
    """每天幾則新聞。"""
    days = [date.today() - timedelta(days=i) for i in range(ATTENTION_DAYS - 1, -1, -1)]
    per_day = await _finnhub_news_by_day(symbol, days)
    return [{"date": d.isoformat(), "count": len(items)} for d, items in zip(days, per_day)]


async def fetch_financials(symbol: str) -> dict:
    """算估值要的東西：年度損益表（營收毛利）、五年日線收盤、季度每股盈餘、財報公布日。

    每股盈餘刻意走 Finnhub 的季度序列而不是 Yahoo 的年度損益表：Yahoo 免費額度
    只給 4 個年度數字，本益比曲線會變成 4 個台階，五年區間實際只蓋到三年半。
    公布日反過來只有 Yahoo 給得到歷史：Finnhub 免費額度的 stock/earnings 沒有日期。
    """
    stmt, quarters, releases = await asyncio.gather(
        asyncio.to_thread(_yahoo_financials, symbol),
        fetch_eps_quarters(symbol),
        asyncio.to_thread(_yahoo_earnings_dates, symbol),
    )
    return {**stmt, "quarters": quarters, "releases": releases}


def _yahoo_earnings_dates(symbol: str) -> list[str]:
    """財報真正公布的日子，由舊到新。

    季末到公布之間的實際落差 15～53 天，各家差很多，硬估一個固定天數會讓
    本益比的換檔時機整段偏掉。
    """
    rows = yf.Ticker(symbol).get_earnings_dates(limit=EARNINGS_DATES_LIMIT)
    if rows is None or rows.empty:
        raise RuntimeError(f"Yahoo 查無 {symbol} 的財報公布日")
    return sorted({i.date().isoformat() for i in rows.index})


def _yahoo_financials(symbol: str) -> dict:
    t = yf.Ticker(symbol)
    stmt = t.income_stmt
    if stmt is None or stmt.empty:
        raise RuntimeError(f"Yahoo 查無 {symbol} 的損益表")
    hist = t.history(period=f"{VALUATION_YEARS}y", interval="1d")
    if hist.empty:
        raise RuntimeError(f"Yahoo 查無 {symbol} 的五年價格")

    def row(name: str) -> dict[str, float]:
        if name not in stmt.index:
            return {}
        return {c.date().isoformat(): float(v)
                for c, v in stmt.loc[name].items() if v == v}

    return {
        "revenue": row("Total Revenue"),
        "gross": row("Gross Profit"),
        "net": row("Net Income"),
        # 每股盈餘的分母。跟上面三個是同一張損益表、同一次呼叫，不多打一次 API。
        # 用稀釋後股數不用基本股數：員工手上還沒換成股票的選擇權遲早會變成股票，
        # 那一份本來就該算進來 —— 用基本股數會把稀釋的效果藏到明年才看到。
        "shares": row("Diluted Average Shares"),
        "closes": [{"date": i.date().isoformat(), "close": round(float(c), 4)}
                   for i, c in hist["Close"].items()],
    }


async def fetch_eps_quarters(symbol: str) -> list[dict]:
    """每一季的每股盈餘，由舊到新。四季一加就是滾動一年的盈餘。"""
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as client:
        r = await _get(client, f"{FINNHUB_BASE}/stock/metric",
                       {"symbol": symbol, "metric": "all", "token": _finnhub_token()})
        rows = r.json().get("series", {}).get("quarterly", {}).get("eps", [])
    if not rows:
        raise RuntimeError(f"Finnhub 查無 {symbol} 的季度每股盈餘")
    return sorted(({"period": x["period"], "eps": x["v"]} for x in rows if x["v"] is not None),
                  key=lambda x: x["period"])


async def fetch_eps_surprises(symbol: str) -> list[dict]:
    """最近幾季「實際每股盈餘 vs 分析師預估」，由新到舊。

    法說會摘要要拿它當地基：模型上網搜到的數字可能是舊的、可能是別家寫錯的，
    這一份是我們自己跟 Finnhub 要的，打架時以它為準。
    """
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as client:
        r = await _get(client, f"{FINNHUB_BASE}/stock/earnings",
                       {"symbol": symbol, "limit": EPS_SURPRISE_QUARTERS,
                        "token": _finnhub_token()})
        rows = r.json()
    if not rows:
        raise RuntimeError(f"Finnhub 查無 {symbol} 的每股盈餘實際值")
    return [{"period": x["period"], "year": x["year"], "quarter": x["quarter"],
             "actual": x["actual"], "estimate": x["estimate"],
             "surprise": x["surprise"], "surprise_pct": round(x["surprisePercent"], 2)}
            for x in rows[:EPS_SURPRISE_QUARTERS] if x.get("actual") is not None]


async def fetch_profile(symbol: str) -> dict:
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as client:
        r = await _get(client, f"{FINNHUB_BASE}/stock/profile2",
                       {"symbol": symbol, "token": _finnhub_token()})
        data = r.json()
    if not data:
        raise RuntimeError(f"Finnhub 查無此代號: {symbol}")
    return {
        "name": data.get("name") or symbol,
        "industry": data.get("finnhubIndustry", ""),
        "logo": data.get("logo", ""),
    }


async def fetch_finnhub_news(symbol: str, start: date, end: date) -> list[dict]:
    per_day = await _finnhub_news_by_day(symbol, _span(start, end))
    return [_from_finnhub(i) for items in per_day for i in items
            if i.get("headline") and i.get("datetime")]


def _from_finnhub(item: dict) -> dict:
    ts = datetime.fromtimestamp(item["datetime"], tz=timezone.utc)
    return {
        "date": ts.date().isoformat(),
        "ts": ts.isoformat(),
        "headline": item["headline"].strip(),
        "source": (item.get("source") or "Unknown").strip(),
        "url": item.get("url", ""),
        "summary": (item.get("summary") or "").strip(),
        "provider": "Finnhub",
    }


async def fetch_yahoo_news(symbol: str) -> list[dict]:
    raw = await asyncio.to_thread(lambda: yf.Ticker(symbol).news)
    return [a for a in (_from_yahoo(i) for i in raw or []) if a]


def _from_yahoo(item: dict) -> dict | None:
    body = item.get("content", item)
    headline = (body.get("title") or "").strip()
    if not headline:
        return None

    published = body.get("pubDate") or body.get("providerPublishTime")
    if isinstance(published, (int, float)):
        ts = datetime.fromtimestamp(published, tz=timezone.utc)
    elif isinstance(published, str):
        ts = datetime.fromisoformat(published.replace("Z", "+00:00"))
    else:
        return None

    provider = body.get("provider")
    source = provider.get("displayName") if isinstance(provider, dict) else body.get("publisher")

    url = body.get("canonicalUrl")
    url = url.get("url") if isinstance(url, dict) else body.get("link", "")

    return {
        "date": ts.date().isoformat(),
        "ts": ts.isoformat(),
        "headline": headline,
        "source": (source or "Yahoo Finance").strip(),
        "url": url or "",
        "summary": (body.get("summary") or "").strip(),
        "provider": "Yahoo Finance",
    }


async def fetch_benchmarks(start: date, end: date) -> dict[str, dict[str, float]]:
    """大盤與全部類股 ETF 的日漲跌 %。一次抓齊，因為抓的當下還不知道這支屬於哪個產業。"""
    tickers = [BENCHMARK_SYMBOL, *SECTOR_ETF.values()]

    def _load() -> dict[str, dict[str, float]]:
        hist = yf.download(
            tickers, start=start.isoformat(), end=end.isoformat(),
            interval="1d", progress=False, auto_adjust=True,
        )["Close"]
        if hist.empty:
            raise RuntimeError("yfinance 查無大盤／類股 ETF 的價格資料")
        pct = hist.pct_change() * 100
        return {
            t: {idx.date().isoformat(): round(float(v), 2)
                for idx, v in pct[t].items() if v == v}
            for t in tickers if t in pct
        }

    return await asyncio.to_thread(_load)


async def fetch_earnings(symbol: str, start: date, end: date) -> list[dict]:
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as client:
        r = await _get(client, f"{FINNHUB_BASE}/calendar/earnings",
                       {"symbol": symbol, "from": start.isoformat(), "to": end.isoformat(),
                        "token": _finnhub_token()})
    return r.json().get("earningsCalendar", [])


async def fetch_fundamentals(symbol: str) -> dict:
    """公司在做什麼、靠什麼賺錢、規模多大。Finnhub 免費 tier 沒有，只能跟 Yahoo 拿。"""

    def _load() -> dict:
        info = yf.Ticker(symbol).info
        if not info.get("sector") and not info.get("longBusinessSummary"):
            raise RuntimeError(f"Yahoo 查無 {symbol} 的公司基本面資料")
        return {
            "sector": info.get("sector", ""),
            "industry": info.get("industry", ""),
            "summary": (info.get("longBusinessSummary") or "").strip(),
            "country": info.get("country", ""),
            "website": info.get("website", ""),
            "employees": info.get("fullTimeEmployees"),
            "market_cap": info.get("marketCap"),
            "pe": info.get("trailingPE"),
            "dividend_yield": info.get("dividendYield"),
            "beta": info.get("beta"),
            "price": info.get("currentPrice"),
            "day_low": info.get("dayLow"),
            "day_high": info.get("dayHigh"),
            "year_low": info.get("fiftyTwoWeekLow"),
            "year_high": info.get("fiftyTwoWeekHigh"),
        }

    return await asyncio.to_thread(_load)


async def fetch_chart(symbol: str, range_key: str, period: str = "") -> dict:
    """Google 財經那種「選區間、算區間漲跌」的走勢資料。

    period 只有要當「對照用的背景線」時才覆寫 —— 背景線必須蓋得住主角那一段，
    不然兩邊時間對不起來。顆粒度（interval）永遠跟著主角走。
    """
    label, default_period, interval = CHART_RANGES[range_key]
    period = period or default_period

    def _load() -> dict:
        hist = yf.Ticker(symbol).history(period=period, interval=interval)
        if hist.empty:
            raise RuntimeError(f"yfinance 查無 {symbol} 的 {label} 價格資料")
        points = [
            {"t": idx.isoformat(), "close": round(float(close), 2)}
            for idx, close in hist["Close"].items()
        ]
        first, last = points[0]["close"], points[-1]["close"]
        return {
            "range": range_key,
            "label": label,
            "intraday": interval.endswith("m"),
            "points": points,
            "first": first,
            "last": last,
            "change": round(last - first, 2),
            "pct": round((last - first) / first * 100, 2),
        }

    return await asyncio.to_thread(_load)


async def fetch_prices(symbol: str, start: date, end: date) -> list[dict]:
    def _load() -> list[dict]:
        hist = yf.Ticker(symbol).history(start=start.isoformat(), end=end.isoformat())
        if hist.empty:
            raise RuntimeError(f"yfinance 查無 {symbol} 的價格資料")
        return [
            {"date": idx.date().isoformat(), "close": round(float(close), 2)}
            for idx, close in hist["Close"].items()
        ]

    return await asyncio.to_thread(_load)


def _yahoo_buyback(symbol: str) -> dict:
    """公司回購：每季花了多少錢買回自己的股票，以及股數到底有沒有真的變少。

    兩個都要。只看金額的話，一家大量發股票給員工、再花同樣的錢買回來的公司，
    看起來會像在回饋股東 —— 其實股數一股沒少，錢只是繞了一圈進員工口袋。
    """
    t = yf.Ticker(symbol)
    cf = t.quarterly_cashflow
    if cf is None or cf.empty or BUYBACK_ROW not in cf.index:
        raise RuntimeError(f"Yahoo 查無 {symbol} 的回購現金流")
    spend = [{"period": c.date().isoformat(), "amount": abs(float(v))}
             for c, v in cf.loc[BUYBACK_ROW].items() if v == v]

    shares = t.get_shares_full(start=_years_ago(BUYBACK_YEARS))
    if shares is None or shares.empty:
        raise RuntimeError(f"Yahoo 查無 {symbol} 的流通股數")
    # 股數沒有還原分割。1 股變 10 股會看起來像暴增 90%，跟回購完全相反的方向。
    adjusted = shares.astype(float)
    for when, ratio in t.splits.items():
        adjusted[adjusted.index < when] *= ratio
    quarterly = adjusted.resample("QE").last().dropna()
    year_end = adjusted.resample("YE").last().dropna()

    annual = t.cashflow
    spend_by_year = (
        {c.date().year: abs(float(v)) for c, v in annual.loc[BUYBACK_ROW].items() if v == v}
        if annual is not None and not annual.empty and BUYBACK_ROW in annual.index
        else {}
    )

    return {
        "spend": sorted(spend, key=lambda r: r["period"], reverse=True),
        "shares": [{"period": d.date().isoformat(), "count": float(v)}
                   for d, v in quarterly.items()],
        "annual": [{"year": y, "amount": a, "shares": _shares_at(year_end, y)}
                   for y, a in sorted(spend_by_year.items())],
        "dividends": [{"date": d.date().isoformat(), "amount": float(v)}
                      for d, v in t.dividends.tail(DIVIDEND_SAMPLE).items()],
        "price": float(t.fast_info["last_price"]),
    }


def _shares_at(year_end, year: int) -> float:
    """那一年年底的流通股數。沒抓到就是 0，讓上層自己決定要不要顯示。"""
    for d, v in year_end.items():
        if d.year == year:
            return float(v)
    return 0.0


def _years_ago(years: int) -> str:
    return (date.today() - timedelta(days=round(365.25 * years))).isoformat()


async def fetch_buyback(symbol: str) -> dict:
    return await asyncio.to_thread(_yahoo_buyback, symbol)


def _yahoo_rotation() -> dict:
    """11 個類股 ETF、大盤，加上風險胃納要用的債券 ETF。一次抓齊。

    這份資料跟你查哪支股票無關 —— 查 AAPL 跟查 CB 算出來一模一樣，所以上層會快取。
    """
    tickers = [BENCHMARK_SYMBOL, *SECTOR_ETF.values(), *RISK_EXTRA_SYMBOLS]
    hist = yf.download(
        tickers, start=(date.today() - timedelta(days=ROTATION_HISTORY_DAYS)).isoformat(),
        interval="1d", progress=False, auto_adjust=True,
    )["Close"].dropna(how="all")
    if hist.empty:
        raise RuntimeError("yfinance 查無類股 ETF 的價格資料")

    # 缺值直接往前補。少數 ETF 偶爾缺一天，為了那一天砍掉所有人的當天資料太浪費
    hist = hist.ffill().dropna()
    return {
        "asof": hist.index[-1].date().isoformat(),
        "closes": {t: [float(v) for v in hist[t]] for t in tickers if t in hist},
    }


async def fetch_rotation() -> dict:
    return await asyncio.to_thread(_yahoo_rotation)


def _yahoo_holdings(etf: str) -> list[dict]:
    """這檔 ETF 前十大成份股。黃金、比特幣那種沒有成份股的，回空 list。"""
    table = yf.Ticker(etf).funds_data.top_holdings
    if table is None or table.empty:
        return []
    return [{"symbol": str(sym), "name": str(row["Name"]),
             "weight": round(float(row["Holding Percent"]) * 100, 1)}
            for sym, row in table.iterrows()]


async def fetch_holdings(etf: str) -> list[dict]:
    return await asyncio.to_thread(_yahoo_holdings, etf)
