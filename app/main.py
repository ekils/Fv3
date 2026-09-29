"""News Radar — 輸入代號，看著它把新聞抓回來，然後只留下值得看的。"""

"""
.venv/bin/uvicorn app.main:app --port 8000
"""



import asyncio
import hashlib
import json
import os
import re
import time
from datetime import date, timedelta
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse

from .analyze import build_report, peers_for, verdict_for
from .buyback import build_buyback
from .calendar import build_calendar
from .macro import build_macro, series_for
from .rotation import build_rotation
from .trend import build_trend
from .valuation import build_valuation, known_eps
from .vix import build_vix
from .config import (
    ATTENTION_SPIKE_RATIO,
    CACHE_TTL_SECONDS,
    BENCHMARK_SYMBOL,
    RISK_EXTRA_SYMBOLS,
    ROTATION_RANGES,
    ROTATION_SLOPE_DAYS,
    SECTOR_ETF,
    CHART_RANGES,
    DEFAULT_CHART_RANGE,
    DEFAULT_NEWS_DAYS,
    VALUATION_MAX_YEARS,
    VALUATION_MIN_YEARS,
    MAX_NEWS_DAYS,
    MIN_NEWS_DAYS,
    VIX_PERIOD_OVERRIDE,
    VIX_SYMBOL,
)
from .sources import (
    fetch_holdings,
    fetch_rotation,
    fetch_buyback,
    fetch_benchmarks,
    fetch_earnings,
    fetch_chart,
    fetch_financials,
    fetch_finnhub_news,
    fetch_fred,
    fetch_fundamentals,
    fetch_news_counts,
    fetch_prices,
    fetch_profile,
    fetch_yahoo_news,
)

load_dotenv()

STATIC = Path(__file__).parent.parent / "static"
SYMBOL_RE = re.compile(r"^[A-Z][A-Z.\-]{0,9}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

app = FastAPI(title="News Radar")
_cache: dict[tuple[str, int], tuple[float, dict]] = {}
# 資金流向跟個股無關，借用同一個快取。代號是空字串，不可能跟真的 (代號, 天數) 撞到
def _rotation_key(days: int) -> tuple[str, int]:
    return ("", days)

# ETF 的成份股一季才換一次，而且只有滑過去才會去抓，所以自己一個小快取就夠
_holdings_cache: dict[str, tuple[float, dict]] = {}
KNOWN_ETFS = {BENCHMARK_SYMBOL, *SECTOR_ETF.values(), *RISK_EXTRA_SYMBOLS}

# Cookie 裡放的是密碼的雜湊，不是密碼本身。翻 DevTools 看不到原文，也推不回去。
SESSION_COOKIE = "radar_session"
LOGIN_BACKGROUNDS = ("purple", "amber", "sage")
OPEN_PATHS = {"/login", "/api/login"} | {f"/login-bg-{n}.png" for n in LOGIN_BACKGROUNDS}


def _session_token() -> str:
    secret = os.environ.get("APP_PASSWORD", "")
    if not secret:
        raise RuntimeError("APP_PASSWORD 沒設。把它寫進 .env，不要寫進程式碼")
    return hashlib.sha256(secret.encode()).hexdigest()


@app.middleware("http")
async def require_login(request: Request, call_next):
    """沒帶對 cookie 就別想拿到任何東西 —— 頁面轉去登入，API 直接 401。

    只擋在這一層。每支 API 各自檢查一次的話，哪天新增一支忘了加，門就開著。
    """
    path = request.url.path
    if path in OPEN_PATHS or request.cookies.get(SESSION_COOKIE) == _session_token():
        return await call_next(request)
    if path.startswith("/api/"):
        return JSONResponse({"error": "請先登入"}, status_code=401)
    return RedirectResponse("/login")


@app.get("/login")
async def login_page():
    return FileResponse(STATIC / "login.html", headers={"Cache-Control": "no-store"})


@app.get("/login-bg-{name}.png")
async def login_bg(name: str):
    """登入頁底圖。擋在登入前面，所以不能走要驗 cookie 的那條路。"""
    if name not in LOGIN_BACKGROUNDS:
        return JSONResponse({"error": f"沒有這個底圖: {name}"}, status_code=404)
    return FileResponse(STATIC / f"login-bg-{name}.png", headers={"Cache-Control": "max-age=86400"})


@app.post("/api/login")
async def login(request: Request):
    body = await request.json()
    if (body.get("password") or "") != os.environ.get("APP_PASSWORD", ""):
        return JSONResponse({"error": "密碼不對"}, status_code=401)
    r = JSONResponse({"ok": True})
    r.set_cookie(SESSION_COOKIE, _session_token(), httponly=True, samesite="lax")
    return r


@app.get("/")
async def root():
    """網址列只打一個斜線的時候，一律送去 /main。

    沒登入的話上面那層 middleware 會在這之前就把人攔去 /login，
    所以這裡不用再判斷一次登入 —— 一個規則，不是兩個。
    """
    return RedirectResponse("/main")


@app.get("/main")
async def index():
    # 前端是單一檔案、改完存檔就要看到結果。讓瀏覽器快取它只會製造「明明改了卻沒變」的假象。
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-store"})


@app.get("/api/chart")
async def chart(symbol: str, range: str = DEFAULT_CHART_RANGE):
    symbol = symbol.strip().upper()
    if not SYMBOL_RE.match(symbol):
        return JSONResponse({"error": f"代號格式不正確: {symbol}"}, status_code=400)
    if range not in CHART_RANGES:
        return JSONResponse({"error": f"區間只支援 {list(CHART_RANGES)}"}, status_code=400)
    try:
        return await fetch_chart(symbol, range)
    except Exception as exc:
        return JSONResponse({"error": f"{type(exc).__name__}: {exc}"[:200]}, status_code=502)


@app.get("/api/calendar")
async def calendar(year: int, month: int, symbol: str = "", sector: str = ""):
    if not 1 <= month <= 12:
        return JSONResponse({"error": f"月份不正確: {month}"}, status_code=400)

    earnings: list[dict] = []
    if symbol:
        symbol = symbol.strip().upper()
        if not SYMBOL_RE.match(symbol):
            return JSONResponse({"error": f"代號格式不正確: {symbol}"}, status_code=400)
        # 抓一整年而不是只抓這個月：財報一季一次，翻到沒有財報的月份時才講得出下一次是哪天。
        span = date(year, month, 1)
        try:
            earnings = await fetch_earnings(symbol, span - timedelta(days=365), span + timedelta(days=365))
        except Exception as exc:
            return JSONResponse({"error": f"{type(exc).__name__}: {exc}"[:200]}, status_code=502)

    return build_calendar(year, month, sector, earnings)


@app.get("/api/compare")
async def compare(symbol: str, range: str = DEFAULT_CHART_RANGE, sector: str = ""):
    """這支 vs 大盤 vs 同業，區間跟走勢圖那排按鈕一致。"""
    symbol = symbol.strip().upper()
    if not SYMBOL_RE.match(symbol):
        return JSONResponse({"error": f"代號格式不正確: {symbol}"}, status_code=400)
    if range not in CHART_RANGES:
        return JSONResponse({"error": f"區間只支援 {list(CHART_RANGES)}"}, status_code=400)

    peers = peers_for(sector)
    try:
        charts = await asyncio.gather(*(fetch_chart(s, range) for s, _ in [(symbol, "")] + peers))
    except Exception as exc:
        return JSONResponse({"error": f"{type(exc).__name__}: {exc}"[:200]}, status_code=502)

    mine, *rest = charts
    rows = [{"symbol": s, "label": label, "pct": c["pct"]}
            for (s, label), c in zip(peers, rest)]
    return {
        "label": mine["label"],
        "from": mine["points"][0]["t"][:10],
        "to": mine["points"][-1]["t"][:10],
        "mine": mine["pct"],
        "rows": rows,
        "verdict": (verdict_for(mine["pct"], rows[-1]["pct"], rows[-1]["label"], symbol)
                    if rows else verdict_for(mine["pct"], None, "")),
    }


@app.get("/api/macro")
async def macro(sector: str = ""):
    """這個產業該盯的總體經濟指標，現在是多少、最近半年往哪走。"""
    try:
        raw = await fetch_fred(series_for(sector))
    except Exception as exc:
        return JSONResponse({"error": f"{type(exc).__name__}: {exc}"[:200]}, status_code=502)
    return build_macro(sector, raw)


@app.get("/api/attention")
async def attention(symbol: str):
    """每天有幾則新聞。只量體溫，不指方向。"""
    symbol = symbol.strip().upper()
    if not SYMBOL_RE.match(symbol):
        return JSONResponse({"error": f"代號格式不正確: {symbol}"}, status_code=400)
    try:
        days = await fetch_news_counts(symbol)
    except Exception as exc:
        return JSONResponse({"error": f"{type(exc).__name__}: {exc}"[:200]}, status_code=502)
    counts = sorted(d["count"] for d in days)
    median = counts[len(counts) // 2]
    return {
        "symbol": symbol,
        "median": median,
        "spike_at": median * ATTENTION_SPIKE_RATIO,
        "days": days,
    }


def _trend_or_error(fin: dict, years: int) -> dict:
    """軌道算不出來不該連本益比一起拖下水，但也不准靜靜消失 —— 把原因送到畫面上。"""
    try:
        return build_trend(fin, known_eps(fin["quarters"], fin["releases"]), years)
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"[:200]}


def _as_of(fin: dict, end: str) -> dict:
    """把股價截到基準日為止。

    回頭看某一天貴不貴的時候，那天之後的價格市場還不知道。不截掉的話本益比區間
    會混進未來資訊，算出來的「當時落在第幾 %」是作弊的答案。
    """
    closes = [c for c in fin["closes"] if c["date"] <= end]
    if len(closes) < 2:
        raise RuntimeError(f"{end} 之前的股價不足兩筆，算不出區間")
    return {**fin, "closes": closes}


@app.get("/api/valuation")
async def valuation(symbol: str, years: int = VALUATION_MAX_YEARS, end: str = ""):
    """本益比，跟這家公司自己過去 years 年比。end 空的就是看今天，給日期就是回到那天看。"""
    symbol = symbol.strip().upper()
    if not SYMBOL_RE.match(symbol):
        return JSONResponse({"error": f"代號格式不正確: {symbol}"}, status_code=400)
    if not VALUATION_MIN_YEARS <= years <= VALUATION_MAX_YEARS:
        return JSONResponse(
            {"error": f"年數只支援 {VALUATION_MIN_YEARS}–{VALUATION_MAX_YEARS}"}, status_code=400)
    if end and not DATE_RE.match(end):
        return JSONResponse({"error": f"基準日格式不正確: {end}"}, status_code=400)
    try:
        fin = await fetch_financials(symbol)
        if end:
            fin = _as_of(fin, end)
        return {"symbol": symbol, "asof": bool(end),
                "years_min": VALUATION_MIN_YEARS, "years_max": VALUATION_MAX_YEARS,
                **build_valuation(fin, years), "trend": _trend_or_error(fin, years)}
    except Exception as exc:
        return JSONResponse({"error": f"{type(exc).__name__}: {exc}"[:200]}, status_code=502)


@app.get("/api/rotation")
async def rotation(days: int = ROTATION_SLOPE_DAYS):
    """錢正在從哪個板塊搬到哪個板塊。跟查哪支股票無關，所以同一個天數共用一份快取。"""
    if days not in {n for _, n in ROTATION_RANGES}:
        return JSONResponse({"error": f"沒有這個區間: {days} 天"}, status_code=400)
    key = _rotation_key(days)
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < CACHE_TTL_SECONDS:
        return hit[1]
    try:
        raw = await fetch_rotation()
        built = build_rotation(raw["closes"], raw["asof"], BENCHMARK_SYMBOL, days)
    except Exception as exc:
        return JSONResponse({"error": f"{type(exc).__name__}: {exc}"[:200]}, status_code=502)
    _cache[key] = (time.time(), built)
    return built


@app.get("/api/holdings")
async def holdings(etf: str):
    """這檔 ETF 裡面裝了哪些股票。只認畫面上真的會出現的那些代號。"""
    etf = etf.strip().upper()
    if etf not in KNOWN_ETFS:
        return JSONResponse({"error": f"這不是畫面上會出現的 ETF: {etf}"}, status_code=400)
    hit = _holdings_cache.get(etf)
    if hit and time.time() - hit[0] < CACHE_TTL_SECONDS:
        return hit[1]
    try:
        rows = await fetch_holdings(etf)
    except Exception as exc:
        return JSONResponse({"error": f"{type(exc).__name__}: {exc}"[:200]}, status_code=502)
    payload = {"etf": etf, "holdings": rows}
    _holdings_cache[etf] = (time.time(), payload)
    return payload


@app.get("/api/buyback")
async def buyback(symbol: str):
    """公司拿自己的錢買回自己的股票 —— 花了多少，以及股數有沒有真的變少。"""
    symbol = symbol.strip().upper()
    if not SYMBOL_RE.match(symbol):
        return JSONResponse({"error": f"代號格式不正確: {symbol}"}, status_code=400)
    try:
        raw = await fetch_buyback(symbol)
        return {"symbol": symbol, **build_buyback(raw, raw["shares"][-1]["count"])}
    except Exception as exc:
        return JSONResponse({"error": f"{type(exc).__name__}: {exc}"[:200]}, status_code=502)


@app.get("/api/vix")
async def vix(symbol: str, range: str = DEFAULT_CHART_RANGE):
    """恐慌指數疊在股價上。區間跟走勢圖那排按鈕一致。"""
    symbol = symbol.strip().upper()
    if not SYMBOL_RE.match(symbol):
        return JSONResponse({"error": f"代號格式不正確: {symbol}"}, status_code=400)
    if range not in CHART_RANGES:
        return JSONResponse({"error": f"區間只支援 {list(CHART_RANGES)}"}, status_code=400)
    try:
        stock, fear = await asyncio.gather(
            fetch_chart(symbol, range),
            fetch_chart(VIX_SYMBOL, range, VIX_PERIOD_OVERRIDE.get(range, "")))
        return {"symbol": symbol, **build_vix(stock, fear)}
    except Exception as exc:
        return JSONResponse({"error": f"{type(exc).__name__}: {exc}"[:200]}, status_code=502)


@app.get("/api/ranges")
async def ranges():
    return {
        "chart": [{"key": k, "label": v[0]} for k, v in CHART_RANGES.items()],
        "chart_default": DEFAULT_CHART_RANGE,
        "news_days": DEFAULT_NEWS_DAYS,
    }


@app.get("/api/search")
async def search(symbol: str, days: int = 14):
    symbol = symbol.strip().upper()
    return StreamingResponse(
        _stream(symbol, days),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _stream(symbol: str, days: int):
    if not SYMBOL_RE.match(symbol):
        yield _sse({"type": "error", "message": f"代號格式不正確: {symbol!r}"})
        return
    if not MIN_NEWS_DAYS <= days <= MAX_NEWS_DAYS:
        yield _sse({"type": "error", "message": f"天數只支援 {MIN_NEWS_DAYS}～{MAX_NEWS_DAYS} 天"})
        return

    cached = _cache.get((symbol, days))
    if cached and time.time() - cached[0] < CACHE_TTL_SECONDS:
        yield _sse({"type": "cached", "age": int(time.time() - cached[0])})
        yield _sse({"type": "result", "report": cached[1]})
        return

    queue: asyncio.Queue = asyncio.Queue()
    worker = asyncio.create_task(_run(symbol, days, queue))
    try:
        while True:
            event = await queue.get()
            yield _sse(event)
            if event["type"] in ("result", "error"):
                return
    finally:
        worker.cancel()


async def _run(symbol: str, days: int, queue: asyncio.Queue):
    end = date.today() + timedelta(days=1)
    start = end - timedelta(days=days + 1)

    async def step(tid, label, group, coro):
        await queue.put({"type": "task_start", "id": tid, "label": label, "group": group})
        t0 = time.perf_counter()
        try:
            result = await coro
        except Exception as exc:
            await queue.put(
                {
                    "type": "task_fail",
                    "id": tid,
                    "error": f"{type(exc).__name__}: {exc}"[:200],
                    "ms": int((time.perf_counter() - t0) * 1000),
                }
            )
            raise
        await queue.put(
            {
                "type": "task_done",
                "id": tid,
                "count": len(result) if isinstance(result, list) else 1,
                "ms": int((time.perf_counter() - t0) * 1000),
            }
        )
        return result

    jobs = (
        ("profile", "Finnhub 公司", "基本資料", lambda: fetch_profile(symbol)),
        ("basics", "Yahoo 基本面", "基本資料", lambda: fetch_fundamentals(symbol)),
        ("prices", "Yahoo 日線", "價格", lambda: fetch_prices(symbol, start, end)),
        ("bench", "大盤對照", "價格", lambda: fetch_benchmarks(start, end)),
        ("finnhub", "Finnhub 新聞", "新聞", lambda: fetch_finnhub_news(symbol, start, end)),
        ("yahoo", "Yahoo 新聞", "新聞", lambda: fetch_yahoo_news(symbol)),
    )
    await queue.put(
        {
            "type": "plan",
            "symbol": symbol,
            "days": days,
            "tasks": [{"id": i, "label": label, "group": group} for i, label, group, _ in jobs],
        }
    )

    profile, fundamentals, prices, bench, finnhub_news, yahoo_news = await asyncio.gather(
        *(step(i, label, group, make()) for i, label, group, make in jobs),
        return_exceptions=True,
    )

    failures = []
    for tid, value in (
        ("profile", profile),
        ("basics", fundamentals),
        ("prices", prices),
        ("bench", bench),
        ("finnhub", finnhub_news),
        ("yahoo", yahoo_news),
    ):
        if isinstance(value, BaseException):
            failures.append({"id": tid, "error": f"{type(value).__name__}: {value}"[:200]})

    if isinstance(profile, BaseException) or isinstance(prices, BaseException):
        await queue.put({"type": "error", "message": failures[0]["error"], "failures": failures})
        return

    articles = []
    for news in (finnhub_news, yahoo_news):
        if not isinstance(news, BaseException):
            articles.extend(a for a in news if start.isoformat() <= a["date"] <= end.isoformat())

    if not articles and len(failures) >= 2:
        await queue.put({"type": "error", "message": "兩個新聞來源都失敗了", "failures": failures})
        return

    if isinstance(fundamentals, BaseException):
        fundamentals = {}
    if isinstance(bench, BaseException):
        bench = {}

    report = build_report(symbol, profile, fundamentals, bench, prices, articles)
    report["days"] = days
    report["failures"] = failures

    await queue.put({"type": "sources", "items": report["source_counts"]})
    await asyncio.sleep(0.4)
    await queue.put({"type": "result", "report": report})
    _cache[(symbol, days)] = (time.time(), report)


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
