"""HttpClient: ретраи на таймауты (kcar отдаёт тело медленно)."""

from typing import Any

import httpx
import pytest

from app.infrastructure.sources.common.http import HttpClient


class _FlakyClient:
    """Первый запрос — ReadTimeout, второй — ответ."""

    def __init__(self, responses: list[Any]) -> None:
        self._responses = list(responses)
        self.calls = 0

    async def __aenter__(self) -> "_FlakyClient":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None

    async def request(self, method: str, url: str, **kwargs: Any) -> Any:
        outcome = self._responses[min(self.calls, len(self._responses) - 1)]
        self.calls += 1
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.mark.asyncio
async def test_read_timeout_retried_then_success(monkeypatch):
    ok = httpx.Response(200, json={"ok": True}, request=httpx.Request("GET", "https://api.example.com/x"))
    flaky = _FlakyClient([httpx.ReadTimeout("slow"), ok])
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: flaky)

    client = HttpClient()
    resp = await client.get("https://api.example.com/x")

    assert resp.status_code == 200
    assert flaky.calls == 2


@pytest.mark.asyncio
async def test_timeouts_exhausted_raises_fetch_error(monkeypatch):
    flaky = _FlakyClient([httpx.ReadTimeout("slow")])
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: flaky)

    from app.infrastructure.sources.common.http import FetchError

    client = HttpClient(max_retries=2)
    with pytest.raises(FetchError):
        await client.get("https://api.example.com/x")
    assert flaky.calls == 2



# tests/adapters/test_http_client.py
import pytest
from app.infrastructure.sources.common.http import FetchError, _backoff, _parse_retry_after


@pytest.mark.asyncio
async def test_retries_5xx_then_succeeds():
    calls = {"n": 0}
    def handler(request):
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(503)
        return httpx.Response(200, json={"ok": True})
    client = HttpClient(
        max_retries=3, transport=httpx.MockTransport(handler)
    )
    r = await client.get("http://x")
    assert r.status_code == 200
    assert calls["n"] == 3


@pytest.mark.asyncio
async def test_does_not_retry_400():
    calls = {"n": 0}
    def handler(request):
        calls["n"] += 1
        return httpx.Response(400)
    client = HttpClient(max_retries=3, transport=httpx.MockTransport(handler))
    with pytest.raises(FetchError, match="HTTP 400"):
        await client.get("http://x")
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_retry_after_header_respected(monkeypatch):
    sleeps = []
    async def fake_sleep(s): sleeps.append(s)
    monkeypatch.setattr("asyncio.sleep", fake_sleep)
    calls = {"n": 0}
    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "7"})
        return httpx.Response(200)
    client = HttpClient(max_retries=2, transport=httpx.MockTransport(handler))
    await client.get("http://x")
    assert sleeps and 7.0 <= sleeps[0] <= 7.0 + 1e-6


@pytest.mark.asyncio
async def test_retries_remote_protocol_error():
    calls = {"n": 0}
    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.RemoteProtocolError("boom")
        return httpx.Response(200)
    client = HttpClient(max_retries=2, transport=httpx.MockTransport(handler))
    r = await client.get("http://x")
    assert r.status_code == 200
    assert calls["n"] == 2


def test_max_retries_zero_rejected():
    with pytest.raises(ValueError):
        HttpClient(max_retries=0)


@pytest.mark.asyncio
async def test_get_json_invalid_json_raises_fetch_error():
    def handler(request):
        return httpx.Response(200, text="<html>captcha</html>")
    client = HttpClient(transport=httpx.MockTransport(handler))
    with pytest.raises(FetchError, match="Invalid JSON"):
        await client.get_json("http://x")


def test_backoff_capped():
    assert _backoff(100) <= 30.0 * 1.5  # cap + max jitter


def test_parse_retry_after_seconds():
    assert _parse_retry_after("7") == 7.0
    assert _parse_retry_after("-1") is None
    assert _parse_retry_after("garbage") is None


def test_parse_retry_after_http_date():
    # будущая дата → положительная задержка
    from email.utils import format_datetime
    from datetime import datetime, timedelta, timezone
    future = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=10))
    v = _parse_retry_after(future)
    assert v is not None and 0 <= v <= 15