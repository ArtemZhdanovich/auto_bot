"""Тесты /api/ai/* (AI-поиск Mini App, JWT) и IDOR архивного диалога.

Regression: пользователь берётся из `user.telegram_id` (PK таблицы users),
а не из несуществующего атрибута `user.id` (иначе AttributeError → 500).
"""

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.db.models import DialogSession, Source, User, UserFilter


@pytest.fixture
def mock_llm(monkeypatch):
    """Подменяет LLM-фабрику на детерминированный MockLLMClient."""
    from app.infrastructure.ai.llm_client import MockLLMClient

    monkeypatch.setattr(
        "app.di.providers.adapters._llm", lambda: MockLLMClient()
    )


async def _add_source(session: AsyncSession, name: str = "encar") -> None:
    """Включает один источник, чтобы `TriggerFetch` не падал."""
    session.add(
        Source(name=name, adapter_class="EncarApiAdapter", kind="api", enabled=True)
    )
    await session.commit()


@pytest.fixture
def queued_task(monkeypatch) -> dict[str, str]:
    """Подменяет постановку задач парсинга: возвращает job-id без RabbitMQ."""
    job = {"id": "job-1"}

    async def fake_enqueue_task(task, *a, **kw):
        return job["id"]

    monkeypatch.setattr(
        "app.di.providers.adapters.enqueue_task", fake_enqueue_task
    )
    return job


async def _archive_row(
    session: AsyncSession, user_id: int, content: str = "Kia Sorento до 35к"
) -> DialogSession:
    """Кладёт архивную сессию диалога."""
    row = DialogSession(
        id=uuid.uuid4(),
        user_id=user_id,
        status="done",
        criteria={"make": "Kia"},
        turn_count=1,
        messages=[{"role": "user", "content": content}],
    )
    session.add(row)
    await session.commit()
    return row


# --- reset / message -----------------------------------------------


@pytest.mark.asyncio
async def test_ai_reset_uses_telegram_id(
    client: AsyncClient, auth_headers: dict[str, str], user: User
):
    """Сброс диалога: 200 (а не 500) — user_id берётся из telegram_id."""
    resp = await client.post("/api/ai/reset", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json() == {"reset": True}


@pytest.mark.asyncio
async def test_ai_message_uses_telegram_id(
    client: AsyncClient, auth_headers: dict[str, str], user: User, mock_llm
):
    """Ход диалога с моком LLM: 200 и ответ агента."""
    resp = await client.post(
        "/api/ai/message", json={"text": "Хочу BMW до 20000"}, headers=auth_headers
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["text"] == "Ok."
    assert body["status"] == "collecting"


# --- finalize ------------------------------------------------------


@pytest.mark.asyncio
async def test_ai_finalize_creates_filter_with_telegram_id(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session: AsyncSession,
    user: User,
    queued_task,
):
    """Финализация: фильтр создаётся с user_id = telegram_id (FK users)."""
    await _add_source(session)
    resp = await client.post(
        "/api/ai/finalize",
        json={"criteria": {"make": "BMW", "price_max": 20000}},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["criteria"]["make"] == "BMW"
    # Контракт `sources` — список источников (бот делает ", ".join).
    assert body["sources"] == ["encar"]
    filters = (
        (
            await session.execute(
                select(UserFilter).where(UserFilter.user_id == user.telegram_id)
            )
        )
        .scalars()
        .all()
    )
    assert len(filters) == 1
    assert filters[0].user_id == user.telegram_id


@pytest.mark.asyncio
async def test_finalize_invalid_criteria_rejected(
    client: AsyncClient, auth_headers: dict[str, str], user: User
):
    r = await client.post(
        "/api/ai/finalize",
        json={"criteria": {"price_max": "not-a-number"}},
        headers=auth_headers,
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_finalize_valid_criteria_accepted(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session: AsyncSession,
    user: User,
    queued_task,
):
    await _add_source(session)
    r = await client.post(
        "/api/ai/finalize",
        json={"criteria": {"make": "Toyota", "price_max": 50000}},
        headers=auth_headers,
    )
    assert r.status_code == 200


# --- валидация и ошибки --------------------------------------------


@pytest.mark.asyncio
async def test_message_text_empty_rejected(
    client: AsyncClient, auth_headers: dict[str, str], user: User
):
    r = await client.post(
        "/api/ai/message", json={"text": ""}, headers=auth_headers
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_message_text_too_long_rejected(
    client: AsyncClient, auth_headers: dict[str, str], user: User
):
    r = await client.post(
        "/api/ai/message", json={"text": "x" * 5000}, headers=auth_headers
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_llm_error_does_not_leak_reason(
    client: AsyncClient, auth_headers: dict[str, str], user: User, monkeypatch
):
    from app.infrastructure.ai.llm_client import LLMError

    def failing(*a, **kw):
        raise LLMError("GLM_API_KEY is not set")

    monkeypatch.setattr(
        "app.di.providers.adapters.get_cached_llm_client", failing
    )
    r = await client.post(
        "/api/ai/message", json={"text": "hi"}, headers=auth_headers
    )
    assert r.status_code == 503
    assert "GLM_API_KEY" not in r.json()["detail"]


@pytest.mark.asyncio
async def test_dialog_error_returns_503(
    client: AsyncClient, auth_headers: dict[str, str], user: User, mock_llm, monkeypatch
):
    from app.infrastructure.ai.agent import DialogError

    async def failing_agent(*a, **kw):
        raise DialogError("LLM timeout")

    monkeypatch.setattr(
        "app.di.providers.adapters.run_agent", failing_agent
    )
    r = await client.post(
        "/api/ai/message", json={"text": "hi"}, headers=auth_headers
    )
    assert r.status_code == 503


# --- rate limit ----------------------------------------------------


@pytest.mark.asyncio
async def test_message_rate_limit(
    client: AsyncClient,
    auth_headers: dict[str, str],
    user: User,
    mock_llm,
):
    """21-е сообщение за минуту → 429."""
    for _ in range(20):
        r = await client.post(
            "/api/ai/message", json={"text": "hi"}, headers=auth_headers
        )
        assert r.status_code != 429
    r = await client.post(
        "/api/ai/message", json={"text": "hi"}, headers=auth_headers
    )
    assert r.status_code == 429


# --- IDOR: чужой диалог → 404 --------------------------------------


@pytest.mark.asyncio
async def test_own_dialog_history_visible(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session: AsyncSession,
    user: User,
):
    row = await _archive_row(session, user.telegram_id)
    r = await client.get(f"/api/ai/dialog/{row.id}", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["session_id"] == str(row.id)
    assert body["messages"][0]["content"] == "Kia Sorento до 35к"


@pytest.mark.asyncio
async def test_foreign_dialog_404(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session: AsyncSession,
    user: User,
):
    """Чужой диалог → 404 (существование не раскрывается)."""
    row = await _archive_row(session, user.telegram_id + 1, content="чужой")
    r = await client.get(f"/api/ai/dialog/{row.id}", headers=auth_headers)
    assert r.status_code == 404
    assert "чужой" not in r.text


@pytest.mark.asyncio
async def test_unknown_dialog_404(
    client: AsyncClient, auth_headers: dict[str, str], user: User
):
    r = await client.get(f"/api/ai/dialog/{uuid.uuid4()}", headers=auth_headers)
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_malformed_dialog_id_404(
    client: AsyncClient, auth_headers: dict[str, str], user: User
):
    r = await client.get("/api/ai/dialog/not-a-uuid", headers=auth_headers)
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_dialog_history_requires_jwt(client: AsyncClient):
    r = await client.get(f"/api/ai/dialog/{uuid.uuid4()}")
    assert r.status_code == 401


# --- кэш LLM-клиента -----------------------------------------------


@pytest.mark.asyncio
async def test_llm_client_cached_across_requests(
    client: AsyncClient, auth_headers: dict[str, str], user: User, monkeypatch
):
    """build_llm_client вызывается один раз на (provider, model)."""
    calls = {"n": 0}

    from app.infrastructure.ai.llm_client import MockLLMClient, get_cached_llm_client

    def counting_build(*a, **kw):
        calls["n"] += 1
        return MockLLMClient()

    monkeypatch.setattr(
        "app.infrastructure.ai.llm_client.build_llm_client", counting_build
    )
    get_cached_llm_client.cache_clear()
    try:
        for _ in range(3):
            await client.post(
                "/api/ai/message", json={"text": "hi"}, headers=auth_headers
            )
        assert calls["n"] == 1
    finally:
        get_cached_llm_client.cache_clear()
