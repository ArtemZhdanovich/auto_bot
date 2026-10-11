"""Тесты перевода тайтлов (PLAN.md, фаза 5): TranslateClient + translate_listings.

Перевод некритичен: любая ошибка → None → UI показывает оригинальный title.
"""

import json
from types import SimpleNamespace
from typing import Any

import httpx

import app.workers.fetch as fetch_mod
from app.services.translate_client import TranslateClient
from tests.conftest import FakeRedis, FakeSession


def _client(
    redis: FakeRedis | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    **kw: Any,
) -> TranslateClient:
    return TranslateClient(
        base_url=kw.pop("base_url", "http://translate.test"),
        redis=redis,
        transport=transport,
        **kw,
    )


# ---------- TranslateClient ----------


async def test_disabled_client_returns_none_without_http():
    client = TranslateClient(base_url=None)
    result = await client.translate(["현대 그랜저"])
    assert result == [None]


async def test_cache_hit_skips_http(fake_redis: FakeRedis):
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"translations": ["x"]})

    client = _client(redis=fake_redis, transport=httpx.MockTransport(handler))
    key = client._cache_key("현대 그랜저", "kor_Hang", "rus_Cyrl")
    fake_redis.data[key] = "Хёндэ Грандёр"

    result = await client.translate(["현대 그랜저"], source_lang="kor_Hang")
    assert result == ["Хёндэ Грандёр"]
    assert calls == 0


async def test_success_translates_and_caches(fake_redis: FakeRedis):
    def handler(request: httpx.Request) -> httpx.Response:
        assert b"texts" in request.read()
        return httpx.Response(200, json={"translations": ["Хёндэ Грандёр"]})

    client = _client(redis=fake_redis, transport=httpx.MockTransport(handler))
    result = await client.translate(["현대 그랜저"], source_lang="kor_Hang")
    assert result == ["Хёндэ Грандёр"]
    key = client._cache_key("현대 그랜저", "kor_Hang", "rus_Cyrl")
    assert fake_redis.data[key] == "Хёндэ Грандёр"


async def test_failure_falls_back_to_none(fake_redis: FakeRedis):
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500)

    client = _client(redis=fake_redis, transport=httpx.MockTransport(handler))
    result = await client.translate(["현대"], source_lang="kor_Hang")
    assert result == [None]
    assert calls == 1  # HTTP 5xx не ретраится


async def test_circuit_breaker_opens_after_failures(fake_redis: FakeRedis):
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500)

    client = _client(
        redis=fake_redis,
        transport=httpx.MockTransport(handler),
        max_retries=0,
    )
    for _ in range(3):  # threshold по умолчанию из настроек = 3
        await client.translate(["현대"], source_lang="kor_Hang")
    assert calls == 3
    # CB открыт: HTTP больше не дёргается, возвращаем None (оригинал)
    await client.translate(["현대"], source_lang="kor_Hang")
    assert calls == 3


async def test_mixed_batch_cached_and_new(fake_redis: FakeRedis):
    def handler(request: httpx.Request) -> httpx.Response:
        # В сервис уходят только некэшированные тексты
        texts = json.loads(request.content)["texts"]
        assert texts == ["기아 카니발"]
        return httpx.Response(200, json={"translations": ["Киа Карнивал"]})

    client = _client(redis=fake_redis, transport=httpx.MockTransport(handler))
    cached_key = client._cache_key("현대 그랜저", "kor_Hang", "rus_Cyrl")
    fake_redis.data[cached_key] = "Хёндэ Грандёр"

    result = await client.translate(
        ["현대 그랜저", "기아 카니발"], source_lang="kor_Hang"
    )
    assert result == ["Хёндэ Грандёр", "Киа Карнивал"]


# ---------- translate_listings (fetch worker) ----------


class _SessionCtx:
    def __init__(self, session: FakeSession) -> None:
        self.session = session

    async def __aenter__(self) -> FakeSession:
        return self.session

    async def __aexit__(self, *args: Any) -> None:
        return None


class StubTranslateClient:
    enabled = True

    def __init__(self, translations: list[str | None]) -> None:
        self.translations = translations
        self.calls: list[list[str]] = []

    async def translate(
        self,
        texts: list[str],
        source_lang: str = "auto",
        target_lang: str | None = None,
    ) -> list[str | None]:
        self.calls.append(texts)
        return self.translations


async def test_translate_listings_task_updates_db(monkeypatch):
    session = FakeSession()
    session.set_results([[SimpleNamespace(id=7, title_raw="현대 그랜저")]])
    monkeypatch.setattr(fetch_mod, "session_factory", lambda: _SessionCtx(session))
    stub = StubTranslateClient(["Хёндэ Грандёр"])

    result = await fetch_mod.translate_listings({"translate": stub}, "encar", [7])

    assert result == {"translated": 1}
    assert stub.calls == [["현대 그랜저"]]
    assert session.committed is True
    params = session.executed_params[0]
    assert params == [{"b_id": 7, "b_ru": "Хёндэ Грандёр"}]
    update_sql = str(session.executed[1])  # [0] — SELECT тайтлов
    assert "title_ru" in update_sql
    assert "source_language" in update_sql


async def test_translate_listings_skipped_without_client():
    result = await fetch_mod.translate_listings({}, "encar", [7])
    assert result == {"translated": 0, "skipped": True}


async def test_translate_listings_keeps_null_on_failure(monkeypatch):
    session = FakeSession()
    session.set_results([[SimpleNamespace(id=7, title_raw="현대 그랜저")]])
    monkeypatch.setattr(fetch_mod, "session_factory", lambda: _SessionCtx(session))
    stub = StubTranslateClient([None])  # сервис недоступен → fallback

    result = await fetch_mod.translate_listings({"translate": stub}, "encar", [7])

    assert result == {"translated": 0}
    assert session.executed_params == []  # UPDATE не выполнялся
    assert session.committed is False


async def test_enqueue_translate_does_not_break_fetch(monkeypatch):
    jobs: list[tuple] = []

    async def ok_enqueue(task, *args):
        jobs.append((task,) + args)
        return "msg-id"

    async def broken_enqueue(task, *args):
        raise RuntimeError("rabbitmq down")

    monkeypatch.setattr(fetch_mod, "enqueue_task", ok_enqueue)
    assert await fetch_mod.enqueue_translate({}, "encar", [7]) is True
    assert jobs == [("translate_listings", "encar", [7])]

    monkeypatch.setattr(fetch_mod, "enqueue_task", broken_enqueue)
    assert await fetch_mod.enqueue_translate({}, "encar", [7]) is False