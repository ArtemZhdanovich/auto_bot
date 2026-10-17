"""API AI-анализа: JWT POST (квота free 1/нед), GET кэша, internal-роутер бота."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.deps import get_current_user
from app.core.deps_internal import check_internal_token
from app.db.session import get_session
from app.db.models import Listing
from app.main import app


class _FakeLLM:
    async def complete(self, messages):
        return SimpleNamespace(
            text=(
                '{"verdict": "deal", "fair_min": 29000, "fair_max": 33000, '
                '"summary": "Цена ниже рынка", "factors": ["медиана 31000"]}'
            ),
            tool_calls=[],
        )

    async def aclose(self):
        return None


@pytest.fixture
def llm_mock(monkeypatch):
    mock = AsyncMock(return_value=None)
    monkeypatch.setattr("app.api.analysis.build_llm_client", lambda settings: _FakeLLM())
    monkeypatch.setattr("app.api.analysis.get_settings", lambda: SimpleNamespace())
    return mock


def _listing_row() -> Listing:
    """Реальная ORM-модель: FakeSession.get сверяет класс объекта."""
    return Listing(
        id=42,
        make="Kia",
        model="Carnival",
        year=2021,
        price=30000.0,
        currency="USD",
        mileage_km=60000,
    )


def _client(user, session, monkeypatch):
    from httpx import ASGITransport, AsyncClient

    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_current_user] = lambda: user
    # internal-роутер: в тестах токен не проверяем
    app.dependency_overrides[check_internal_token] = lambda: None
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    return client, session


@pytest.mark.asyncio
async def test_post_analysis_201_and_saved(monkeypatch, fake_session):
    monkeypatch.setattr("app.api.analysis.build_llm_client", lambda settings: _FakeLLM())
    monkeypatch.setattr("app.api.analysis.get_settings", lambda: SimpleNamespace())
    user = SimpleNamespace(telegram_id=111111, subscription_tier="free", subscription_until=None)
    fake_session.add(_listing_row())
    fake_session.set_results([[], [], []])  # квота 0 → comps нет → история нет
    client, _ = _client(user, fake_session, monkeypatch)
    try:
        resp = await client.post("/api/listings/42/analysis")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["verdict"] == "deal"
        assert data["fair_min"] == 29000 and data["fair_max"] == 33000
        assert data["source"] == "ai"
        assert fake_session.committed
        assert len(fake_session.added) == 2  # listing + PriceAnalysis
    finally:
        await client.aclose()
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_post_analysis_free_quota_403(monkeypatch, fake_session):
    monkeypatch.setattr("app.api.analysis.build_llm_client", lambda settings: _FakeLLM())
    monkeypatch.setattr("app.api.analysis.get_settings", lambda: SimpleNamespace())
    user = SimpleNamespace(telegram_id=111111, subscription_tier="free", subscription_until=None)
    fake_session.add(_listing_row())
    fake_session.set_results([["used"]])  # scalar_one()=1 → квота исчерпана
    client, _ = _client(user, fake_session, monkeypatch)
    try:
        resp = await client.post("/api/listings/42/analysis")
        assert resp.status_code == 403
        assert "quota_exceeded" in resp.json()["detail"]
    finally:
        await client.aclose()
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_post_analysis_pro_unlimited(monkeypatch, fake_session):
    monkeypatch.setattr("app.api.analysis.build_llm_client", lambda settings: _FakeLLM())
    monkeypatch.setattr("app.api.analysis.get_settings", lambda: SimpleNamespace())
    until = datetime.now(UTC) + timedelta(days=30)
    user = SimpleNamespace(telegram_id=111111, subscription_tier="pro", subscription_until=until)
    fake_session.add(_listing_row())
    fake_session.set_results([[], [], []])  # pro: квота не проверяется вовсе
    client, _ = _client(user, fake_session, monkeypatch)
    try:
        resp = await client.post("/api/listings/42/analysis")
        assert resp.status_code == 200, resp.text
    finally:
        await client.aclose()
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_get_analysis_cached(fake_session):
    row = SimpleNamespace(payload={"verdict": "fair", "price": 100, "currency": "USD",
                                  "fair_min": 90, "fair_max": 110, "summary": None,
                                  "factors": [], "comps": None, "history": [], "source": "ai"})
    fake_session.add(_listing_row())
    fake_session.set_results([[row]])
    user = SimpleNamespace(telegram_id=111111, subscription_tier="free", subscription_until=None)
    client, _ = _client(user, fake_session, None)
    try:
        resp = await client.get("/api/listings/42/analysis")
        assert resp.status_code == 200
        assert resp.json()["verdict"] == "fair"
    finally:
        await client.aclose()
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_get_analysis_empty_returns_null(fake_session):
    fake_session.add(_listing_row())
    fake_session.set_results([[]])
    user = SimpleNamespace(telegram_id=111111, subscription_tier="free", subscription_until=None)
    client, _ = _client(user, fake_session, None)
    try:
        resp = await client.get("/api/listings/42/analysis")
        assert resp.status_code == 200 and resp.json() is None
    finally:
        await client.aclose()
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_post_analysis_missing_listing_404(monkeypatch, fake_session):
    monkeypatch.setattr("app.api.analysis.build_llm_client", lambda settings: _FakeLLM())
    monkeypatch.setattr("app.api.analysis.get_settings", lambda: SimpleNamespace())
    user = SimpleNamespace(telegram_id=111111, subscription_tier="free", subscription_until=None)
    client, _ = _client(user, fake_session, monkeypatch)
    try:
        resp = await client.post("/api/listings/999/analysis")
        assert resp.status_code == 404
    finally:
        await client.aclose()
        app.dependency_overrides.clear()

# ---------- Гостям AI-анализ закрыт ----------


@pytest.mark.asyncio
async def test_post_analysis_guest_403(fake_session):
    """Гость (id < 0, без email) получает 403 — анализ только авторизированным."""
    user = SimpleNamespace(
        telegram_id=-123456789, email=None, subscription_tier="free", subscription_until=None
    )
    fake_session.add(_listing_row())
    client, _ = _client(user, fake_session, None)
    try:
        resp = await client.post("/api/listings/42/analysis")
        assert resp.status_code == 403
        assert "guest_forbidden" in resp.json()["detail"]
        # LLM даже не вызывался: разбор не создан
        assert len(fake_session.added) == 1  # только listing
    finally:
        await client.aclose()
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_post_analysis_email_user_allowed(monkeypatch, fake_session):
    """Email-пользователь тоже с id < 0, но с email → гость не он, разбор идёт."""
    monkeypatch.setattr("app.api.analysis.build_llm_client", lambda settings: _FakeLLM())
    monkeypatch.setattr("app.api.analysis.get_settings", lambda: SimpleNamespace())
    user = SimpleNamespace(
        telegram_id=-987654321,
        email="user@mail.com",
        subscription_tier="free",
        subscription_until=None,
    )
    fake_session.add(_listing_row())
    fake_session.set_results([[], [], []])
    client, _ = _client(user, fake_session, monkeypatch)
    try:
        resp = await client.post("/api/listings/42/analysis")
        assert resp.status_code == 200, resp.text
        assert resp.json()["verdict"] == "deal"
    finally:
        await client.aclose()
        app.dependency_overrides.clear()
