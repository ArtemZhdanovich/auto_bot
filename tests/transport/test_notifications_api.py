"""Тесты /api/notifications: internal-токен, настройки режима, запуск скана."""

import logging

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.di.providers import adapters as deps_module
from app.infrastructure.db.models import User, UserFilter


@pytest.fixture
def internal_headers(internal_token: str) -> dict[str, str]:
    """Заголовки с internal-токеном (значение из настроек приложения)."""
    return {"X-Internal-Token": internal_token}


# --- auth ----------------------------------------------------------


@pytest.mark.asyncio
async def test_settings_requires_internal_token(client: AsyncClient):
    r = await client.post(
        "/api/notifications/settings", json={"user_id": 1, "notify_mode": "off"}
    )
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_scan_requires_internal_token(client: AsyncClient):
    r = await client.post("/api/notifications/scan")
    assert r.status_code == 401


# --- валидация входа ----------------------------------------------


@pytest.mark.asyncio
async def test_settings_invalid_mode_422(
    client: AsyncClient, internal_headers: dict[str, str]
):
    r = await client.post(
        "/api/notifications/settings",
        json={"user_id": 1, "notify_mode": "sometimes"},
        headers=internal_headers,
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_settings_user_id_zero_rejected(
    client: AsyncClient, internal_headers: dict[str, str]
):
    r = await client.post(
        "/api/notifications/settings",
        json={"user_id": 0},
        headers=internal_headers,
    )
    assert r.status_code == 422


# --- 404 / 403 -----------------------------------------------------


@pytest.mark.asyncio
async def test_settings_404_for_missing_user(
    client: AsyncClient, internal_headers: dict[str, str]
):
    r = await client.post(
        "/api/notifications/settings",
        json={"user_id": 999999999},
        headers=internal_headers,
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_settings_403_for_inactive(
    client: AsyncClient, internal_headers: dict[str, str], inactive_user: User
):
    r = await client.post(
        "/api/notifications/settings",
        json={"user_id": inactive_user.telegram_id},
        headers=internal_headers,
    )
    assert r.status_code == 403


# --- tier bypass ---------------------------------------------------


@pytest.mark.asyncio
async def test_free_user_cannot_set_instant(
    client: AsyncClient,
    internal_headers: dict[str, str],
    session: AsyncSession,
    free_user: User,
):
    """Free + instant → 403 (как в filters.py::create_filter)."""
    session.add(
        UserFilter(
            user_id=free_user.telegram_id,
            name="f1",
            criteria={},
            notify_mode="digest",
        )
    )
    await session.commit()
    r = await client.post(
        "/api/notifications/settings",
        json={"user_id": free_user.telegram_id, "notify_mode": "instant"},
        headers=internal_headers,
    )
    assert r.status_code == 403
    assert "Business" in r.json()["detail"]


@pytest.mark.asyncio
async def test_business_user_can_set_instant(
    client: AsyncClient,
    internal_headers: dict[str, str],
    session: AsyncSession,
    business_user: User,
):
    session.add(
        UserFilter(
            user_id=business_user.telegram_id,
            name="f1",
            criteria={},
            notify_mode="digest",
        )
    )
    await session.commit()
    r = await client.post(
        "/api/notifications/settings",
        json={"user_id": business_user.telegram_id, "notify_mode": "instant"},
        headers=internal_headers,
    )
    assert r.status_code == 200
    assert r.json()["updated"] == 1


# --- quiet_hours валидация ----------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "qh",
    ["23:00-08:00", "00:00-23:59", "08:00-08:30"],
)
async def test_quiet_hours_valid(
    client: AsyncClient,
    internal_headers: dict[str, str],
    user: User,
    qh: str,
):
    r = await client.post(
        "/api/notifications/settings",
        json={"user_id": user.telegram_id, "quiet_hours": qh},
        headers=internal_headers,
    )
    assert r.status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "qh",
    ["abc", "25:00-08:00", "23:00", "23:00-", "23:00-08:00-extra", "23:00-23:00"],
)
async def test_quiet_hours_invalid(
    client: AsyncClient,
    internal_headers: dict[str, str],
    user: User,
    qh: str,
):
    r = await client.post(
        "/api/notifications/settings",
        json={"user_id": user.telegram_id, "quiet_hours": qh},
        headers=internal_headers,
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_quiet_hours_conflict_with_clear(
    client: AsyncClient, internal_headers: dict[str, str], user: User
):
    """Оба переданы — 422, а не тихий приоритет одного."""
    r = await client.post(
        "/api/notifications/settings",
        json={
            "user_id": user.telegram_id,
            "quiet_hours": "23:00-08:00",
            "quiet_hours_clear": True,
        },
        headers=internal_headers,
    )
    assert r.status_code == 422
    assert "mutually exclusive" in r.json()["detail"]


# --- bulk update ---------------------------------------------------


@pytest.mark.asyncio
async def test_settings_off_updates_filters(
    client: AsyncClient,
    internal_headers: dict[str, str],
    session: AsyncSession,
    user: User,
):
    """Нет фильтров — ничего не обновлено, но ответ 200."""
    r = await client.post(
        "/api/notifications/settings",
        json={"user_id": user.telegram_id, "notify_mode": "off"},
        headers=internal_headers,
    )
    assert r.status_code == 200
    assert r.json() == {"user_id": user.telegram_id, "updated": 0, "detail": None}
    filters = (
        (await session.execute(select(UserFilter).where(UserFilter.user_id == user.telegram_id)))
        .scalars()
        .all()
    )
    assert filters == []


@pytest.mark.asyncio
async def test_settings_updates_all_filters(
    client: AsyncClient,
    internal_headers: dict[str, str],
    session: AsyncSession,
    user: User,
):
    for i in range(3):
        session.add(
            UserFilter(
                user_id=user.telegram_id,
                name=f"f{i}",
                criteria={},
                notify_mode="digest",
            )
        )
    await session.commit()

    r = await client.post(
        "/api/notifications/settings",
        json={"user_id": user.telegram_id, "notify_mode": "off"},
        headers=internal_headers,
    )
    assert r.json()["updated"] == 3


@pytest.mark.asyncio
async def test_settings_nothing_to_change(
    client: AsyncClient,
    internal_headers: dict[str, str],
    session: AsyncSession,
    user: User,
):
    """Пустой patch — не трогаем updated_at у N фильтров."""
    session.add(
        UserFilter(
            user_id=user.telegram_id, name="f", criteria={}, notify_mode="digest"
        )
    )
    await session.commit()
    r = await client.post(
        "/api/notifications/settings",
        json={"user_id": user.telegram_id},
        headers=internal_headers,
    )
    assert r.json()["updated"] == 0
    assert r.json()["detail"] == "nothing_to_change"


# --- scan: идемпотентность -----------------------------------------


@pytest.mark.asyncio
async def test_scan_dedup_409(
    client: AsyncClient,
    internal_headers: dict[str, str],
    fake_redis,
    monkeypatch,
):
    """Второй POST в течение минуты — 409."""

    async def fake_enqueue(task, *a, **kw):
        return "job-1"

    monkeypatch.setattr(deps_module, "enqueue_task", fake_enqueue)
    await fake_redis.delete("notify:scan:dedup")

    r1 = await client.post("/api/notifications/scan", headers=internal_headers)
    assert r1.status_code == 200
    r2 = await client.post("/api/notifications/scan", headers=internal_headers)
    assert r2.status_code == 409


@pytest.mark.asyncio
async def test_scan_503_on_queue_failure(
    client: AsyncClient,
    internal_headers: dict[str, str],
    fake_redis,
    monkeypatch,
):
    """enqueue_task падает — 503, dedup-ключ снят."""

    async def failing_enqueue(*a, **kw):
        raise RuntimeError("rmq down")

    monkeypatch.setattr(deps_module, "enqueue_task", failing_enqueue)
    await fake_redis.delete("notify:scan:dedup")

    r = await client.post("/api/notifications/scan", headers=internal_headers)
    assert r.status_code == 503
    # Ключ снят — можно повторить
    assert await fake_redis.exists("notify:scan:dedup") == 0


# --- логирование ---------------------------------------------------


@pytest.mark.asyncio
async def test_settings_logged(
    client: AsyncClient,
    internal_headers: dict[str, str],
    user: User,
    caplog,
):
    with caplog.at_level(logging.INFO):
        await client.post(
            "/api/notifications/settings",
            json={"user_id": user.telegram_id, "notify_mode": "off"},
            headers=internal_headers,
        )
    assert "notification_settings_updated" in caplog.text
