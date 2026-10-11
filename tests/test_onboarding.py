"""Тесты онбординга: POST /api/me/onboarded (UI/UX ТЗ §2.8, миграция 0007)."""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.core.deps import get_current_user
from app.db.session import get_session
from app.main import app

from conftest import FakeSession


def _user(onboarded_at: datetime | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        telegram_id=111111,
        username="test",
        first_name="Test",
        last_name=None,
        language_code="ru",
        role="buyer",
        subscription_tier="free",
        subscription_until=None,
        created_at=datetime.now(UTC),
        is_active=True,
        onboarded_at=onboarded_at,
    )


@pytest.fixture
def client():
    from httpx import ASGITransport, AsyncClient

    session = FakeSession()
    user = _user()
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_current_user] = lambda: user

    yield AsyncClient(transport=ASGITransport(app=app), base_url="http://test"), session, user
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_onboarded_sets_flag(client):
    http, session, user = client
    assert user.onboarded_at is None
    resp = await http.post("/api/me/onboarded")
    assert resp.status_code == 200
    body = resp.json()
    assert body["onboarded_at"] is not None
    assert user.onboarded_at is not None
    assert session.committed is True


@pytest.mark.asyncio
async def test_onboarded_idempotent(client):
    http, session, user = client
    original = datetime.now(UTC)
    user.onboarded_at = original
    resp = await http.post("/api/me/onboarded")
    assert resp.status_code == 200
    assert user.onboarded_at is original  # не перезаписан
    assert session.committed is False


@pytest.mark.asyncio
async def test_me_includes_onboarded(client):
    http, _, user = client
    resp = await http.get("/api/me")
    assert resp.status_code == 200
    assert resp.json()["onboarded_at"] is None
