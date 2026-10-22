"""TECHDEBT 7.2: BackendClient — retry (сеть/5xx) и circuit breaker."""

import httpx

from app.transport.bot.client import BackendClient


def _client_with_handler(handler, **kw) -> BackendClient:
    c = BackendClient(base_url="http://backend", **kw)
    c._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return c


async def test_retry_on_500_then_success():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(500)
        return httpx.Response(200, json={"ok": True})

    c = _client_with_handler(handler)
    assert await c._post("/api/dialog/reset", {"user_id": 1}) == {"ok": True}
    assert calls["n"] == 2
    assert c._failures == 0  # успех закрыл счётчик


async def test_no_retry_on_4xx():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(403, json={"detail": "quota"})

    c = _client_with_handler(handler)
    assert await c._post("/x", {}) is None
    assert calls["n"] == 1  # 4xx не ретраится


async def test_circuit_opens_after_threshold():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    c = _client_with_handler(handler, timeout=0.1)
    for _ in range(BackendClient.CB_THRESHOLD):
        await c._post("/x", {})
    assert c._breaker_open() is True
    calls = {"n": 0}

    def counting_handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json={})

    c._client = httpx.AsyncClient(transport=httpx.MockTransport(counting_handler))
    assert await c._get("/y") is None  # breaker открыт → запрос не идёт
    assert calls["n"] == 0


async def test_long_call_no_retry():
    """LLM-диалог (таймаут > дефолта) не ретраится — задвоение хода опасно."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(500)

    c = _client_with_handler(handler, timeout=1.0)
    assert await c.dialog_message(1, "привет") is None
    assert calls["n"] == 1


async def test_shared_http_client_reused():
    c = BackendClient(base_url="http://backend")
    first = c._http()
    assert c._http() is first
    await c.aclose()
    assert c._client is None