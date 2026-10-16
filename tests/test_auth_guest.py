"""Гостевой режим: POST /api/auth/guest — сайт открыт без регистрации."""

import pytest

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


@pytest.mark.asyncio
async def test_guest_creates_user_and_tokens(client):
    """Гость создаётся без email/пароля: имя «Гость», синтетический id < 0."""
    http, session = client
    resp = await http.post("/api/auth/guest")
    assert resp.status_code == 201
    body = resp.json()
    assert body["access_token"] and body["refresh_token"]
    assert body["user"]["first_name"] == "Гость"
    assert body["user"]["telegram_id"] < 0
    assert body["user"]["subscription_tier"] == "free"
    assert len(session.added) == 1
    assert session.added[0].email is None and session.added[0].password_hash is None


@pytest.mark.asyncio
async def test_guest_token_grants_me(client):
    """Гостевой access-токен проходит как обычный JWT → /me отдаёт профиль."""
    http, session = client
    token = (await http.post("/api/auth/guest")).json()["access_token"]
    resp = await http.get("/api/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["first_name"] == "Гость"
