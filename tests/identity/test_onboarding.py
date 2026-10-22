"""Тесты онбординга: POST /api/me/onboarded (UI/UX ТЗ §2.8, миграция 0007).

Эндпоинт делает атомарный `UPDATE ... WHERE onboarded_at IS NULL`, поэтому
тесты идут на реальной сессии (`client`/`user` из conftest): стаб FakeSession
не применяет UPDATE к in-memory объекту и не может проверить идемпотентность.
"""

import pytest


@pytest.mark.asyncio
async def test_onboarded_sets_flag(client, session, user, auth_headers):
    assert user.onboarded_at is None
    resp = await client.post("/api/me/onboarded", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["onboarded_at"] is not None
    await session.refresh(user)
    assert user.onboarded_at is not None


@pytest.mark.asyncio
async def test_onboarded_idempotent(client, session, user, auth_headers):
    """Повторный вызов не перезаписывает время первого онбординга."""
    first = await client.post("/api/me/onboarded", headers=auth_headers)
    assert first.status_code == 200
    original = first.json()["onboarded_at"]

    resp = await client.post("/api/me/onboarded", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["onboarded_at"] == original


@pytest.mark.asyncio
async def test_me_includes_onboarded(client, auth_headers):
    resp = await client.get("/api/me", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["onboarded_at"] is None
