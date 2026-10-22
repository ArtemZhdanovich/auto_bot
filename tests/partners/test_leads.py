"""Тесты лидов: создание ботом (internal), смена статуса, хук lead_created."""

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.db.models import AnalyticsEvent, Lead, User
from app.infrastructure.db.repositories.analytics import (
    SqlAlchemyAnalyticsRepository,
)
from app.infrastructure.services import analytics


@pytest.fixture
def internal_headers(internal_token: str) -> dict[str, str]:
    """Заголовки с internal-токеном (значение из настроек приложения)."""
    return {"X-Internal-Token": internal_token}


async def _events(session: AsyncSession, user_id: int) -> list[AnalyticsEvent]:
    """События воронки пользователя `user_id` из БД."""
    return list(
        (
            await session.execute(
                select(AnalyticsEvent).where(AnalyticsEvent.user_id == user_id)
            )
        )
        .scalars()
        .all()
    )


@pytest.fixture
async def manager(session: AsyncSession) -> User:
    """Менеджер, которому можно назначить лид (валидация `manager_id`)."""
    manager = User(
        telegram_id=900042,
        username="fixture_manager",
        role="admin",
        subscription_tier="business",
        is_active=True,
    )
    session.add(manager)
    await session.flush()
    return manager


# --- auth ----------------------------------------------------------


@pytest.mark.asyncio
async def test_create_lead_requires_internal_token(client: AsyncClient):
    resp = await client.post("/api/leads", json={"user_id": 1})
    assert resp.status_code == 401


# --- create --------------------------------------------------------


@pytest.mark.asyncio
async def test_create_lead_and_event(
    client: AsyncClient,
    internal_headers: dict[str, str],
    session: AsyncSession,
    user: User,
    listing,
):
    resp = await client.post(
        "/api/leads",
        json={
            "user_id": user.telegram_id,
            "listing_id": listing.id,
            "source": "bot",
            "comment": "Хочу посмотреть",
        },
        headers=internal_headers,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "new"
    assert body["user_id"] == user.telegram_id
    assert body["listing_id"] == listing.id

    events = await _events(session, user.telegram_id)
    assert len(events) == 1
    assert events[0].name == analytics.EVENT_LEAD_CREATED
    assert events[0].props["listing_id"] == listing.id
    assert events[0].props["source"] == "bot"


@pytest.mark.asyncio
async def test_create_lead_minimal(
    client: AsyncClient,
    internal_headers: dict[str, str],
    user: User,
):
    resp = await client.post(
        "/api/leads", json={"user_id": user.telegram_id}, headers=internal_headers
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["listing_id"] is None and body["source"] is None


@pytest.mark.asyncio
async def test_create_lead_missing_user_404(
    client: AsyncClient, internal_headers: dict[str, str], listing
):
    r = await client.post(
        "/api/leads",
        json={"user_id": 999999999, "listing_id": listing.id, "source": "bot"},
        headers=internal_headers,
    )
    assert r.status_code == 404
    assert "User" in r.json()["detail"]


@pytest.mark.asyncio
async def test_create_lead_missing_listing_404(
    client: AsyncClient, internal_headers: dict[str, str], user: User
):
    r = await client.post(
        "/api/leads",
        json={"user_id": user.telegram_id, "listing_id": 999999, "source": "bot"},
        headers=internal_headers,
    )
    assert r.status_code == 404
    assert "Listing" in r.json()["detail"]


# --- create: идемпотентность -------------------------------------


@pytest.mark.asyncio
async def test_create_lead_idempotency_key(
    client: AsyncClient,
    internal_headers: dict[str, str],
    user: User,
    listing,
):
    payload = {
        "user_id": user.telegram_id,
        "listing_id": listing.id,
        "source": "bot",
    }
    headers = {**internal_headers, "Idempotency-Key": "key-1"}
    r1 = await client.post("/api/leads", json=payload, headers=headers)
    r2 = await client.post("/api/leads", json=payload, headers=headers)
    assert r1.status_code == 201
    assert r2.status_code == 201  # возвращает existing
    assert r1.json()["id"] == r2.json()["id"]  # один и тот же лид


@pytest.mark.asyncio
@pytest.mark.skip(
    reason="нужны два независимых соединения: shared-session harness "
    "не допускает конкурентные запросы (ветка IntegrityError)"
)
async def test_create_lead_race_same_key(
    client: AsyncClient,
    internal_headers: dict[str, str],
    user: User,
    listing,
):
    """Параллельные POST с одним Idempotency-Key → один лид."""


# --- create: track failure не 500 --------------------------------


@pytest.mark.asyncio
async def test_create_lead_track_failure_returns_201(
    client: AsyncClient,
    internal_headers: dict[str, str],
    user: User,
    listing,
    monkeypatch,
):
    """Аналитика недоступна — лид всё равно создаётся (201).

    Патчим репозиторий аналитики: `TrackEvent` глотает ошибку записи,
    поэтому проверяется реальный инвариант «аналитика не ломает поток».
    """

    async def failing_add_event(*a, **kw):
        raise RuntimeError("analytics down")

    monkeypatch.setattr(
        SqlAlchemyAnalyticsRepository, "add_event", failing_add_event
    )
    r = await client.post(
        "/api/leads",
        json={
            "user_id": user.telegram_id,
            "listing_id": listing.id,
            "source": "bot",
        },
        headers=internal_headers,
    )
    assert r.status_code == 201  # лид создан, track не помешал


# --- update -------------------------------------------------------


@pytest.mark.asyncio
async def test_update_lead_status(
    client: AsyncClient,
    internal_headers: dict[str, str],
    session: AsyncSession,
    user: User,
    manager: User,
):
    created = await client.post(
        "/api/leads", json={"user_id": user.telegram_id}, headers=internal_headers
    )
    lead_id = created.json()["id"]

    resp = await client.patch(
        f"/api/leads/{lead_id}",
        json={"status": "in_progress", "manager_id": manager.telegram_id},
        headers=internal_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "in_progress"
    assert body["manager_id"] == manager.telegram_id

    # смена статуса не создаёт событий воронки
    events = await _events(session, user.telegram_id)
    assert len(events) == 1  # только lead_created из create


@pytest.mark.asyncio
async def test_update_missing_lead_404(
    client: AsyncClient, internal_headers: dict[str, str]
):
    resp = await client.patch(
        "/api/leads/999", json={"status": "closed"}, headers=internal_headers
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_invalid_status_422(
    client: AsyncClient, internal_headers: dict[str, str], lead: Lead
):
    resp = await client.patch(
        f"/api/leads/{lead.id}",
        json={"status": "bogus"},
        headers=internal_headers,
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
@pytest.mark.skip(
    reason="нужны два независимых соединения: shared-session harness "
    "не допускает конкурентные запросы"
)
async def test_update_lead_concurrent_status_one_wins(
    client: AsyncClient, internal_headers: dict[str, str], lead: Lead
):
    """Два параллельных PATCH: последний выигрывает, но не теряется."""


@pytest.mark.asyncio
async def test_update_lead_comment_only_keeps_status(
    client: AsyncClient, internal_headers: dict[str, str], lead: Lead
):
    """PATCH без `status`: комментарий обновляется, статус не затирается."""
    r = await client.patch(
        f"/api/leads/{lead.id}",
        json={"comment": "updated"},
        headers=internal_headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["comment"] == "updated"
    assert body["status"] == lead.status


@pytest.mark.asyncio
async def test_update_lead_empty_body_422(
    client: AsyncClient, internal_headers: dict[str, str], lead: Lead
):
    """Пустой PATCH — 422: no-op не должен писать audit-лог."""
    r = await client.patch(
        f"/api/leads/{lead.id}",
        json={},
        headers=internal_headers,
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_update_lead_invalid_manager_422(
    client: AsyncClient, internal_headers: dict[str, str], lead: Lead
):
    r = await client.patch(
        f"/api/leads/{lead.id}",
        json={"manager_id": 999999999},
        headers=internal_headers,
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_update_lead_id_zero_rejected(
    client: AsyncClient, internal_headers: dict[str, str]
):
    r = await client.patch(
        "/api/leads/0", json={"status": "closed"}, headers=internal_headers
    )
    assert r.status_code == 422
