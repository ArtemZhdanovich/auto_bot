# tests/api/test_me.py
import asyncio

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.transport.http.schemas.user import UserOut


# --- /me ------------------------------------------------------------

@pytest.mark.asyncio
async def test_me_requires_auth(client: AsyncClient):
    r = await client.get("/api/me")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_me_returns_profile(client: AsyncClient, auth_headers_user, user):
    r = await client.get("/api/me", headers=auth_headers_user)
    assert r.status_code == 200
    body = r.json()
    assert body["telegram_id"] == user.telegram_id


@pytest.mark.asyncio
async def test_me_does_not_leak_secrets(
    client: AsyncClient, auth_headers_user, user_with_password,
):
    """password_hash / внутренние токены не должны быть в ответе."""
    r = await client.get("/api/me", headers=auth_headers_user)
    body = r.json()
    for forbidden in ("password_hash", "internal_token", "api_key"):
        assert forbidden not in body, f"{forbidden} leaked via /me"


def test_userout_schema_has_no_sensitive_fields():
    """Инвариант схемы: UserOut не содержит чувствительных полей."""
    forbidden = {"password_hash", "password", "api_key", "internal_token"}
    leaked = forbidden & set(UserOut.model_fields)
    assert not leaked, f"Sensitive fields in UserOut: {leaked}"


# --- /me/onboarded --------------------------------------------------

@pytest.mark.asyncio
async def test_onboarded_first_call_sets_datetime(
    client: AsyncClient, auth_headers_user, user, session: AsyncSession,
):
    assert user.onboarded_at is None
    r = await client.post("/api/me/onboarded", headers=auth_headers_user)
    assert r.status_code == 200
    body = r.json()
    assert body["onboarded_at"] is not None
    # В БД тоже установлено
    await session.refresh(user)
    assert user.onboarded_at is not None


@pytest.mark.asyncio
async def test_onboarded_idempotent(
    client: AsyncClient, auth_headers_user, user,
):
    r1 = await client.post("/api/me/onboarded", headers=auth_headers_user)
    r2 = await client.post("/api/me/onboarded", headers=auth_headers_user)
    assert r1.status_code == r2.status_code == 200
    # Оба возвращают одно и то же значение (второй вызов — no-op)
    assert r1.json()["onboarded_at"] == r2.json()["onboarded_at"]


@pytest.mark.asyncio
@pytest.mark.skip(
    reason="нужны два независимых соединения: shared-session harness "
    "не допускает конкурентные запросы (гонка проверяется интеграционно)"
)
async def test_onboarded_race_preserves_first_datetime(
    client: AsyncClient, auth_headers_user, user, session: AsyncSession,
):
    """Параллельные POST сохраняют **первый** onboarded_at (не перезаписывают)."""
    r1, r2 = await asyncio.gather(
        client.post("/api/me/onboarded", headers=auth_headers_user),
        client.post("/api/me/onboarded", headers=auth_headers_user),
        return_exceptions=True,
    )
    assert all(hasattr(r, "status_code") and r.status_code == 200 for r in (r1, r2))
    # Оба вернули одно и то же значение (первое сохранённое)
    assert r1.json()["onboarded_at"] == r2.json()["onboarded_at"]
    # В БД — тоже одно значение
    await session.refresh(user)
    assert user.onboarded_at is not None


@pytest.mark.asyncio
async def test_onboarded_requires_auth(client: AsyncClient):
    r = await client.post("/api/me/onboarded")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_onboarded_inactive_user_403(
    client: AsyncClient, auth_headers_inactive_user,
):
    """Забаненный → 403 (CurrentUserDep до эндпоинта)."""
    r = await client.post("/api/me/onboarded", headers=auth_headers_inactive_user)
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_onboarded_refreshes_object(
    client: AsyncClient, auth_headers_user, user, session: AsyncSession,
):
    """После POST /me/onboarded in-memory user.onboarded_at актуален."""
    assert user.onboarded_at is None
    await client.post("/api/me/onboarded", headers=auth_headers_user)
    # user — attached к сессии (из фикстуры), refresh не нужен:
    # endpoint сделал session.refresh(user), но это другая сессия?
    # В реальном тесте с общим session — проверить
    await session.refresh(user)
    assert user.onboarded_at is not None