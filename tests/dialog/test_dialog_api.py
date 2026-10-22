"""Тесты /api/dialog: internal-токен, ход диалога, reset, finalize, IDOR-границы.

Контроллер тонкий, поэтому тесты бьют по реальным сценариям и адаптерам
(Redis — `FakeRedis`, БД — тестовая сессия), подменяя только LLM/очередь.
"""

import pathlib
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.dialog.use_cases import TriggerFetch
from app.domain.dialog.state import new_state
from app.domain.search.criteria import SearchCriteria
from app.infrastructure.ai.agent import AgentReply
from app.infrastructure.cache.dialog_store import dialog_key
from app.infrastructure.db.models import Source, User


@pytest.fixture
def internal_headers(internal_token) -> dict[str, str]:
    """Заголовки с internal-токеном (значение из настроек приложения)."""
    return {"X-Internal-Token": internal_token}


@pytest.fixture
def mock_llm(monkeypatch):
    """Подменяет LLM-фабрику на детерминированный MockLLMClient."""
    from app.infrastructure.ai.llm_client import MockLLMClient

    monkeypatch.setattr(
        "app.di.providers.adapters._llm", lambda: MockLLMClient()
    )


def _criteria() -> SearchCriteria:
    """Критерии поиска для прямых вызовов `TriggerFetch`."""
    return SearchCriteria(make="Kia", price_max=30000, country="KR")


def _reply(text: str = "Какой бюджет?", options: list[str] | None = None) -> AgentReply:
    """Ответ агента с валидным UUID сессии (иначе архив не запишется)."""
    return AgentReply(
        session_id=str(uuid.uuid4()),
        text=text,
        options=options or ["до 30к"],
        turn_count=1,
        criteria={"make": "Kia"},
    )


async def _add_sources(session: AsyncSession, names: list[str]) -> None:
    """Кладёт в БД включённые источники."""
    for name in names:
        session.add(
            Source(
                name=name,
                adapter_class="EncarApiAdapter",
                kind="api",
                enabled=True,
            )
        )
    await session.commit()


class _Sources:
    """Заглушка `EnabledSourcesPort`."""

    def __init__(self, names: list[str]) -> None:
        self._names = names

    async def enabled_source_names(self) -> list[str]:
        return list(self._names)


class _Queue:
    """Заглушка `FetchQueuePort` с управляемыми падениями."""

    def __init__(self, fail_on: tuple[str, ...] = ()) -> None:
        self.calls: list[tuple[str, dict]] = []
        self._fail_on = set(fail_on)

    async def enqueue_source(self, name: str, payload: dict) -> None:
        self.calls.append((name, payload))
        if name in self._fail_on:
            raise RuntimeError("rmq timeout")


# --- auth ----------------------------------------------------------


@pytest.mark.asyncio
async def test_dialog_requires_internal_token(client: AsyncClient):
    r = await client.post(
        "/api/dialog/message", json={"user_id": 1, "text": "hi"}
    )
    assert r.status_code == 401


# --- архитектурная граница контроллеров ----------------------------


def test_routers_have_no_infra_or_domain_imports() -> None:
    """Тонкие контроллеры: ни SQL/Redis, ни domain/infrastructure в коде."""
    for name in ("dialog", "ai_search", "analysis"):
        text = pathlib.Path(
            f"app/transport/http/routers/{name}.py"
        ).read_text(encoding="utf-8")
        assert "app.infrastructure" not in text, name
        assert "app.domain" not in text, name
        assert "SessionDep" not in text, name
        assert "RedisDep" not in text, name
        assert ".commit()" not in text, name


# --- валидация входа ----------------------------------------------


@pytest.mark.asyncio
async def test_message_text_empty_rejected(
    client: AsyncClient, internal_headers: dict[str, str]
):
    r = await client.post(
        "/api/dialog/message",
        json={"user_id": 1, "text": ""},
        headers=internal_headers,
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_message_text_too_long_rejected(
    client: AsyncClient, internal_headers: dict[str, str]
):
    r = await client.post(
        "/api/dialog/message",
        json={"user_id": 1, "text": "x" * 5000},
        headers=internal_headers,
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_message_user_not_found_404(
    client: AsyncClient, internal_headers: dict[str, str]
):
    r = await client.post(
        "/api/dialog/message",
        json={"user_id": 999999999, "text": "hi"},
        headers=internal_headers,
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_message_deactivated_user_403(
    client: AsyncClient, internal_headers: dict[str, str], inactive_user: User
):
    r = await client.post(
        "/api/dialog/message",
        json={"user_id": inactive_user.telegram_id, "text": "hi"},
        headers=internal_headers,
    )
    assert r.status_code == 403


# --- ход диалога ---------------------------------------------------


@pytest.mark.asyncio
async def test_message_endpoint(
    client: AsyncClient,
    internal_headers: dict[str, str],
    user: User,
    mock_llm,
    monkeypatch,
):
    async def fake_run_agent(user_id, text, **kwargs):
        assert text == "хочу kia sorento"
        return _reply()

    monkeypatch.setattr(
        "app.di.providers.adapters.run_agent", fake_run_agent
    )
    resp = await client.post(
        "/api/dialog/message",
        json={"user_id": user.telegram_id, "text": "хочу kia sorento"},
        headers=internal_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["text"] == "Какой бюджет?"
    assert body["options"] == ["до 30к"]
    assert body["finalize_ready"] is False


@pytest.mark.asyncio
async def test_message_llm_unavailable_503(
    client: AsyncClient, internal_headers: dict[str, str], user: User, monkeypatch
):
    from app.infrastructure.ai.llm_client import LLMError

    def fake_build(*a, **kw):
        raise LLMError("no key")

    monkeypatch.setattr(
        "app.di.providers.adapters.get_cached_llm_client", fake_build
    )
    resp = await client.post(
        "/api/dialog/message",
        json={"user_id": user.telegram_id, "text": "х"},
        headers=internal_headers,
    )
    assert resp.status_code == 503


# --- reset / finalize ----------------------------------------------


@pytest.mark.asyncio
async def test_reset_endpoint(
    client: AsyncClient, internal_headers: dict[str, str], fake_redis
):
    fake_redis.data[dialog_key(77)] = new_state(77).model_dump_json()
    resp = await client.post(
        "/api/dialog/reset", json={"user_id": 77}, headers=internal_headers
    )
    assert resp.status_code == 200
    assert resp.json() == {"reset": True}
    assert dialog_key(77) not in fake_redis.data


@pytest.mark.asyncio
async def test_finalize_endpoint(
    client: AsyncClient,
    internal_headers: dict[str, str],
    fake_redis,
    session: AsyncSession,
    user: User,
    monkeypatch,
):
    fake_redis.data[dialog_key(user.telegram_id)] = new_state(
        user.telegram_id
    ).model_dump_json()
    await _add_sources(session, ["encar"])

    async def fake_enqueue(*args, **kwargs):
        return "job-1"

    monkeypatch.setattr(
        "app.di.providers.adapters.enqueue_task", fake_enqueue
    )
    resp = await client.post(
        "/api/dialog/finalize",
        json={
            "user_id": user.telegram_id,
            "criteria": {"make": "Kia", "price_max": 30000, "country": "KR"},
        },
        headers=internal_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["sources"] == ["encar"]
    assert body["criteria"]["make"] == "Kia"
    assert dialog_key(user.telegram_id) not in fake_redis.data


@pytest.mark.asyncio
async def test_finalize_invalid_criteria_rejected(
    client: AsyncClient, internal_headers: dict[str, str]
):
    r = await client.post(
        "/api/dialog/finalize",
        json={"user_id": 1, "criteria": {"price_max": "not-a-number"}},
        headers=internal_headers,
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_finalize_none_criteria_accepted(
    client: AsyncClient,
    internal_headers: dict[str, str],
    session: AsyncSession,
    user: User,
    monkeypatch,
):
    """`criteria=None` — валидно (finalize использует state из Redis)."""
    await _add_sources(session, ["encar"])

    async def fake_enqueue(*args, **kwargs):
        return "job-1"

    monkeypatch.setattr(
        "app.di.providers.adapters.enqueue_task", fake_enqueue
    )
    r = await client.post(
        "/api/dialog/finalize",
        json={"user_id": user.telegram_id, "criteria": None},
        headers=internal_headers,
    )
    assert r.status_code == 200


# --- пост-обработка best-effort -----------------------------------


@pytest.mark.asyncio
async def test_archive_failure_does_not_500(
    client: AsyncClient,
    internal_headers: dict[str, str],
    mock_llm,
    monkeypatch,
    fake_redis,
    user: User,
):
    """Если запись архива падает, пользователь всё равно получает ответ."""
    from app.infrastructure.db.repositories.dialog import (
        SqlAlchemyDialogRepository,
    )

    async def failing_archive(*a, **kw):
        raise RuntimeError("db down")

    async def ok_agent(*a, **kw):
        return _reply("Какой бюджет?")

    monkeypatch.setattr(SqlAlchemyDialogRepository, "archive", failing_archive)
    monkeypatch.setattr("app.di.providers.adapters.run_agent", ok_agent)
    fake_redis.data[dialog_key(user.telegram_id)] = new_state(
        user.telegram_id
    ).model_dump_json()
    r = await client.post(
        "/api/dialog/message",
        json={"user_id": user.telegram_id, "text": "hi"},
        headers=internal_headers,
    )
    assert r.status_code == 200  # не 500


@pytest.mark.asyncio
async def test_track_failure_does_not_500(
    client: AsyncClient,
    internal_headers: dict[str, str],
    mock_llm,
    monkeypatch,
    user: User,
):
    async def failing_track(*a, **kw):
        raise RuntimeError("analytics down")

    async def ok_agent(*a, **kw):
        return _reply("Какой бюджет?")

    monkeypatch.setattr("app.di.providers.adapters.track", failing_track)
    monkeypatch.setattr("app.di.providers.adapters.run_agent", ok_agent)
    r = await client.post(
        "/api/dialog/message",
        json={"user_id": user.telegram_id, "text": "hi"},
        headers=internal_headers,
    )
    assert r.status_code == 200


# --- TriggerFetch: изоляция ошибок --------------------------------


@pytest.mark.asyncio
async def test_trigger_fetch_partial_failure_returns_queued():
    """3 из 5 источников упали — quota не откатывается, возврат ok."""
    names = [f"src{i}" for i in range(5)]
    queue = _Queue(fail_on=("src1", "src3", "src4"))
    result = await TriggerFetch(_Sources(names), queue).execute(_criteria())
    assert list(result) == ["src0", "src2"]
    assert len(queue.calls) == 5


@pytest.mark.asyncio
async def test_trigger_fetch_all_failed_raises():
    """Все источники упали — raise (agent откатит quota)."""
    queue = _Queue(fail_on=("src0", "src1"))
    with pytest.raises(RuntimeError, match="failed to enqueue any"):
        await TriggerFetch(_Sources(["src0", "src1"]), queue).execute(_criteria())


@pytest.mark.asyncio
async def test_trigger_fetch_no_sources_raises():
    with pytest.raises(RuntimeError, match="no enabled sources"):
        await TriggerFetch(_Sources([]), _Queue()).execute(_criteria())


# --- LLM client cached ---------------------------------------------


@pytest.mark.asyncio
async def test_llm_client_cached(
    client: AsyncClient,
    internal_headers: dict[str, str],
    user: User,
    monkeypatch,
):
    """build_llm_client вызывается один раз на (provider, model)."""
    calls = {"n": 0}

    from app.infrastructure.ai.llm_client import MockLLMClient, get_cached_llm_client

    def counting(*a, **kw):
        calls["n"] += 1
        return MockLLMClient()

    monkeypatch.setattr(
        "app.infrastructure.ai.llm_client.build_llm_client", counting
    )
    get_cached_llm_client.cache_clear()
    try:
        for _ in range(3):
            await client.post(
                "/api/dialog/message",
                json={"user_id": user.telegram_id, "text": "hi"},
                headers=internal_headers,
            )
        assert calls["n"] == 1
    finally:
        get_cached_llm_client.cache_clear()


# --- LLM error не утекает -----------------------------------------


@pytest.mark.asyncio
async def test_llm_error_does_not_leak(
    client: AsyncClient, internal_headers: dict[str, str], user: User, monkeypatch
):
    from app.infrastructure.ai.llm_client import LLMError

    def failing(*a, **kw):
        raise LLMError("GLM_API_KEY is not set")

    monkeypatch.setattr(
        "app.di.providers.adapters.get_cached_llm_client", failing
    )
    r = await client.post(
        "/api/dialog/message",
        json={"user_id": user.telegram_id, "text": "hi"},
        headers=internal_headers,
    )
    assert r.status_code == 503
    assert "GLM_API_KEY" not in r.json()["detail"]
