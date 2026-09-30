import asyncio
import os

import httpx
import pytest

from app.config import HTTP_RETRY_ATTEMPTS
from app.main import why
from app.sources import _get

URL = "https://example.test/x"


def call(statuses):
    """依序回這些狀態碼。回傳 (結果或例外, 實際打了幾次)。"""
    tries = []

    def handler(request):
        tries.append(request)
        return httpx.Response(statuses[min(len(tries) - 1, len(statuses) - 1)], json={"ok": 1})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await _get(client, URL, {"api_key": "s3cret"})

    try:
        return asyncio.run(run()), len(tries)
    except Exception as exc:
        return exc, len(tries)


def test_a_transient_502_is_asked_again_instead_of_killing_the_card():
    # FRED 真的會這樣：同一個 series 前一秒 502、下一秒 200。第一次就放棄的話，
    # 整張總經卡會因為對方打了個嗝而變成一行錯誤訊息
    r, tries = call([502, 200])
    assert r.status_code == 200 and tries == 2


def test_a_wrong_request_fails_immediately_without_retrying():
    # 400／404 是我們問錯了，再問幾次答案都一樣 —— 重試只是把錯誤延後三秒才看到
    exc, tries = call([404])
    assert isinstance(exc, httpx.HTTPStatusError) and tries == 1


def test_an_upstream_that_stays_broken_gives_up_and_raises():
    exc, tries = call([503])
    assert isinstance(exc, httpx.HTTPStatusError) and tries == HTTP_RETRY_ATTEMPTS


def test_the_error_shown_on_screen_never_contains_the_api_key(monkeypatch):
    """httpx 的錯誤訊息裡有完整網址，網址裡有 api_key。原本會被原封不動印在畫面上。"""
    monkeypatch.setenv("FRED_API_KEY", "789c43a1da58")
    exc = httpx.HTTPStatusError(
        "Server error '502 Bad Gateway' for url "
        "'https://api.stlouisfed.org/fred/series/observations?api_key=789c43a1da58'",
        request=None, response=None)
    text = why(exc)
    assert "789c43a1da58" not in text
    assert "<已隱藏>" in text and "502" in text


def test_an_error_with_no_secret_in_it_is_left_alone(monkeypatch):
    monkeypatch.setenv("FRED_API_KEY", "789c43a1da58")
    assert why(RuntimeError("Yahoo 查無 GOOGL 的損益表")) == "RuntimeError: Yahoo 查無 GOOGL 的損益表"


@pytest.mark.parametrize("name", ["FRED_API_KEY", "FINNHUB_API_KEY", "APP_PASSWORD", "ASK_API_KEY"])
def test_every_secret_we_hold_is_scrubbed(monkeypatch, name):
    monkeypatch.setenv(name, "topsecret")
    assert "topsecret" not in why(RuntimeError("boom topsecret boom"))


def test_an_unset_secret_does_not_turn_every_empty_string_into_a_mask(monkeypatch):
    # os.environ.get 回 "" 的話，"".replace 會把遮罩塞進每一個字元的縫隙裡
    for n in ("FRED_API_KEY", "FINNHUB_API_KEY", "APP_PASSWORD", "ASK_API_KEY"):
        monkeypatch.delenv(n, raising=False)
    assert why(RuntimeError("abc")) == "RuntimeError: abc"
