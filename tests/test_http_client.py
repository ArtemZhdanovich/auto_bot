"""HttpClient: ретраи на таймауты (kcar отдаёт тело медленно)."""

from typing import Any

import httpx
import pytest

from app.adapters.common.http import HttpClient


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

    from app.adapters.common.http import FetchError

    client = HttpClient(max_retries=2)
    with pytest.raises(FetchError):
        await client.get("https://api.example.com/x")
    assert flaky.calls == 2
