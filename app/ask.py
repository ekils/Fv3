"""問問題：法說會摘要，以及針對這家公司的追問。

兩件事一定要分清楚：

**逐字稿不是我們抓的。** Finnhub 的 `stock/transcripts` 是付費端點，免費金鑰一律回
403，所以「用 Finnhub 抓 earnings call」這條路走不通（實測過了）。改成讓模型自己上網
把那場法說會找出來 —— 這反而更完整，因為它連分析師 Q&A 的報導都讀得到。

**但模型不准憑記憶掰數字。** 每一次請求都附一段「已查證事實」：財報日、最近四季的
每股盈餘實際 vs 預估、公司屬於哪個產業 —— 全部是本系統剛剛從 Finnhub／Yahoo 抓回來
的。模型搜到的東西跟這段打架時，以這段為準。
"""

import asyncio
import os
from datetime import date, timedelta

import httpx

from .config import (
    ASK_BRIEF_SECTIONS,
    ASK_BRIEF_TASK,
    ASK_ENDPOINT,
    ASK_MODEL,
    ASK_RESPONSES_APIVERSION,
    ASK_SESSION_ROUNDS,
    ASK_SYSTEM,
    ASK_TIMEOUT_SECONDS,
)
from .sources import fetch_earnings, fetch_eps_surprises, fetch_profile


def _api_key() -> str:
    key = os.environ.get("ASK_API_KEY", "").strip()
    if not key:
        raise RuntimeError("ASK_API_KEY 未設定，請填進 .env")
    return key


def _quarter(releases: list[dict]) -> str:
    """最近一次已經公布的財報是哪一季，寫成「Q2 2026」給搜尋用。"""
    if not releases:
        return "latest"
    last = releases[0]
    return f"Q{last['quarter']} {last['year']}"


def facts(symbol: str, profile: dict, surprises: list[dict], upcoming: list[dict]) -> str:
    """一段純文字的「已查證事實」。模型搜到的東西跟這裡打架時，以這裡為準。"""
    lines = [f"代號：{symbol}",
             f"公司名稱：{profile.get('name', symbol)}",
             f"產業（Finnhub 分類）：{profile.get('industry') or '未提供'}",
             f"今天的日期：{date.today().isoformat()}"]
    if surprises:
        lines.append("最近幾季每股盈餘（EPS，來源 Finnhub，由新到舊）：")
        for s in surprises:
            beat = "超前" if s["surprise"] >= 0 else "落後"
            lines.append(
                f"  {s['year']} Q{s['quarter']}（截至 {s['period']}）："
                f"實際 {s['actual']}，分析師預估 {s['estimate']}，"
                f"{beat} {abs(s['surprise_pct'])}%")
    if upcoming:
        lines.append(f"下一次財報公布日（Finnhub）：{upcoming[0].get('date', '未提供')}")
    return "\n".join(lines)


def brief_prompt(symbol: str, name: str, quarter: str) -> str:
    sections = "\n".join(f"### {title}\n{how}" for title, how in ASK_BRIEF_SECTIONS)
    return ASK_BRIEF_TASK.format(symbol=symbol, name=name, quarter=quarter, sections=sections)


def _text(payload: dict) -> tuple[str, list[dict]]:
    """從 Responses API 的回應裡挖出「那段字」跟「它引用了哪些網址」。

    output 是一個陣列，裡面混著 reasoning、web_search_call、message 好幾種東西。
    我們只要 message，其他的是它工作的過程。
    """
    said, cites = [], []
    for item in payload.get("output", []):
        if item.get("type") != "message":
            continue
        for part in item.get("content", []):
            said.append(part.get("text", ""))
            for note in part.get("annotations", []):
                if note.get("url"):
                    cites.append({"title": note.get("title") or note["url"], "url": note["url"]})
    answer = "\n".join(t for t in said if t).strip()
    if not answer:
        raise RuntimeError(f"模型沒有給出內容（status={payload.get('status')}）")
    # 同一個網址被引好幾次很常見，去重但保留第一次出現的順序
    seen, unique = set(), []
    for c in cites:
        if c["url"] not in seen:
            seen.add(c["url"])
            unique.append(c)
    return answer, unique


async def _call(turns: list[dict]) -> dict:
    """丟給 Azure OpenAI 的 Responses API，開著 web_search。

    走 Responses API 不是為了趕流行 —— chat/completions 那條路不支援 web_search，
    模型只能憑訓練資料回答，那正是使用者明講不要的。
    """
    url = f"{ASK_ENDPOINT.rstrip('/')}/openai/v1/responses"
    body = {"model": ASK_MODEL, "instructions": ASK_SYSTEM, "input": turns,
            "tools": [{"type": "web_search"}]}
    async with httpx.AsyncClient(timeout=ASK_TIMEOUT_SECONDS) as client:
        r = await client.post(url, params={"api-version": ASK_RESPONSES_APIVERSION},
                              headers={"api-key": _api_key()}, json=body)
        r.raise_for_status()
        payload = r.json()
    answer, cites = _text(payload)
    return {"answer": answer, "sources": cites,
            "searched": any(i.get("type", "").startswith("web_search")
                            for i in payload.get("output", []))}


async def ground(symbol: str) -> dict:
    """抓這家公司的即時事實。三個請求平行打，不是一個等一個。"""
    today = date.today()
    profile, surprises, upcoming = await asyncio.gather(
        fetch_profile(symbol),
        fetch_eps_surprises(symbol),
        fetch_earnings(symbol, today, today + timedelta(days=120)),
    )
    return {"name": profile.get("name", symbol),
            "quarter": _quarter(surprises),
            "facts": facts(symbol, profile, surprises, upcoming)}


def turns(known: str, history: list[dict], question: str) -> list[dict]:
    """把「已查證事實 + 前幾輪對話 + 這次的問題」排成模型吃的順序。

    事實放在最前面當第一輪，不是塞在每一句問題裡 —— 塞在每句裡的話，十輪下來
    同一段事實會被重複送十次，又貴又容易讓模型抓錯版本。
    """
    head = [{"role": "user", "content": f"【已查證事實】\n{known}"},
            {"role": "assistant", "content": "收到，我會以這段事實為準，其他的我上網查。"}]
    recent = history[-ASK_SESSION_ROUNDS * 2:]
    return head + recent + [{"role": "user", "content": question}]


async def summarise(symbol: str) -> dict:
    """開啟對話視窗時自動跑的那一份法說會摘要。"""
    g = await ground(symbol)
    out = await _call(turns(g["facts"], [], brief_prompt(symbol, g["name"], g["quarter"])))
    return {**out, "name": g["name"], "quarter": g["quarter"], "facts": g["facts"]}


async def reply(symbol: str, known: str, history: list[dict], question: str) -> dict:
    """使用者在輸入框打的追問。"""
    return await _call(turns(known, history, question))
