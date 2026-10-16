"""Тесты /api/notifications: internal-токен, настройки режима, запуск скана."""

import pytest

from app.core.deps_internal import get_redis, internal_token
from app.db.session import get_session
from app.main import app

from conftest import FakeRedis, FakeSession

HEADERS = {"X-Internal-Token": internal_token()}


@pytest.fixture
def client(fake_redis: FakeRedis):
    from httpx import ASGITransport, AsyncClient

    app.dependency_overrides[get_redis] = lambda: fake_redis
    session = FakeSession()
    app.dependency_overrides[get_session] = lambda: session

    yield AsyncClient(transport=ASGITransport(app=app), base_url="http://test"), session
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_settings_requires_internal_token(client):
    http, _ = client
    resp = await http.post(
        "/api/notifications/settings", json={"user_id": 1, "notify_mode": "off"}
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_settings_off_updates_filters(client):
    http, session = client
    resp = await http.post(
        "/api/notifications/settings",
        json={"user_id": 100, "notify_mode": "off"},
        headers=HEADERS,
    )
    assert resp.status_code == 200
    # FakeSession возвращает пустой список фильтров
    assert resp.json() == {"user_id": 100, "updated": 0}
    assert session.committed is True


@pytest.mark.asyncio
async def test_settings_invalid_mode_422(client):
    http, _ = client
    resp = await http.post(
        "/api/notifications/settings",
        json={"user_id": 1, "notify_mode": "sometimes"},
        headers=HEADERS,
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_scan_requires_internal_token(client):
    http, _ = client
    resp = await http.post("/api/notifications/scan")
    assert resp.status_code == 401