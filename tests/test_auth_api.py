"""Тесты /api/auth/refresh: обмен refresh-токена на новую пару (веб-сессии)."""

import pytest

from app.core.config import get_settings
from app.core.security import create_access_token, create_refresh_token
from app.db.models import User
from app.db.session import get_session
from app.main import app

from conftest import FakeSession


@pytest.fixture
def client():
    from httpx import ASGITransport, AsyncClient

    session = FakeSession()
    app.dependency_overrides[get_session] = lambda: session
    yield AsyncClient(transport=ASGITransport(app=app), base_url="http://test"), session
    app.dependency_overrides.clear()


def _user() -> User:
    # is_active задаём явно: Python-side default SQLAlchemy применяется при
    # flush, а без БД объект остаётся с is_active=None.
    return User(
        telegram_id=111111, username="webuser", subscription_tier="free", is_active=True
    )


@pytest.mark.asyncio
async def test_refresh_returns_new_token_pair(client):
    http, session = client
    session.add(_user())
    settings = get_settings()
    token = create_refresh_token(111111, settings)
    resp = await http.post("/api/auth/refresh", json={"refresh_token": token})
    assert resp.status_code == 200
    body = resp.json()
    assert body["access_token"]
    assert body["refresh_token"]
    assert body["token_type"] == "bearer"


@pytest.mark.asyncio
async def test_access_token_rejected_as_refresh(client):
    """Access-токен в поле refresh_token не принимается (type mismatch)."""
    http, session = client
    session.add(_user())
    settings = get_settings()
    token = create_access_token(111111, settings)
    resp = await http.post("/api/auth/refresh", json={"refresh_token": token})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_garbage_token_401(client):
    http, _ = client
    resp = await http.post(
        "/api/auth/refresh", json={"refresh_token": "not-a-jwt-value"}
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_unknown_user_401(client):
    """Валидный refresh-токен, но пользователя нет/деактивирован → 401."""
    http, session = client
    settings = get_settings()
    token = create_refresh_token(999999, settings)
    resp = await http.post("/api/auth/refresh", json={"refresh_token": token})
    assert resp.status_code == 401
