"""Тесты лидов: создание ботом (internal), смена статуса, хук lead_created."""

import pytest

from app.core.deps_internal import internal_token
from app.db.models import AnalyticsEvent
from app.db.session import get_session
from app.main import app
from app.services import analytics
from conftest import FakeSession

HEADERS = {"X-Internal-Token": internal_token()}


@pytest.fixture
def client(fake_session: FakeSession):
    from httpx import ASGITransport, AsyncClient

    app.dependency_overrides[get_session] = lambda: fake_session
    yield AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_create_lead_requires_internal_token(client):
    resp = await client.post("/api/leads", json={"user_id": 1})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_create_lead_and_event(client, fake_session):
    resp = await client.post(
        "/api/leads",
        json={"user_id": 7, "listing_id": 123, "source": "bot", "comment": "Хочу посмотреть"},
        headers=HEADERS,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "new"
    assert body["user_id"] == 7
    assert body["listing_id"] == 123

    events = [x for x in fake_session.added if isinstance(x, AnalyticsEvent)]
    assert len(events) == 1
    assert events[0].name == analytics.EVENT_LEAD_CREATED
    assert events[0].user_id == 7
    assert events[0].props == {"listing_id": 123, "source": "bot"}


@pytest.mark.asyncio
async def test_create_lead_minimal(client, fake_session):
    resp = await client.post("/api/leads", json={"user_id": 8}, headers=HEADERS)
    assert resp.status_code == 201
    body = resp.json()
    assert body["listing_id"] is None and body["source"] is None


@pytest.mark.asyncio
async def test_update_lead_status(client, fake_session):
    created = await client.post(
        "/api/leads", json={"user_id": 9}, headers=HEADERS
    )
    lead_id = created.json()["id"]

    resp = await client.patch(
        f"/api/leads/{lead_id}",
        json={"status": "in_progress", "manager_id": 42},
        headers=HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "in_progress"
    assert body["manager_id"] == 42

    # смена статуса не создаёт событий воронки
    events = [x for x in fake_session.added if isinstance(x, AnalyticsEvent)]
    assert len(events) == 1  # только lead_created из create


@pytest.mark.asyncio
async def test_update_missing_lead_404(client):
    resp = await client.patch(
        "/api/leads/999", json={"status": "closed"}, headers=HEADERS
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_invalid_status_422(client):
    resp = await client.patch(
        "/api/leads/1", json={"status": "bogus"}, headers=HEADERS
    )
    assert resp.status_code == 422
