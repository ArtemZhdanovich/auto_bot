"""Тесты /api/dialog: internal-токен, ход диалога, reset, finalize."""

import pytest

from app.main import app
from app.core.config import get_settings
from app.core.deps_internal import get_redis
from app.db.session import get_session
from app.services.dialog import new_state

from conftest import FakeRedis, FakeSession

HEADERS = {"X-Internal-Token": get_settings().app_secret_key}


@pytest.fixture
def client(fake_redis: FakeRedis):
    from httpx import ASGITransport, AsyncClient

    app.dependency_overrides[get_redis] = lambda: fake_redis
    app.dependency_overrides[get_session] = lambda: FakeSession()

    yield AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_dialog_requires_internal_token(client):
    resp = await client.post("/api/dialog/message", json={"user_id": 1, "text": "хочу kia"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_message_endpoint(monkeypatch, fake_redis, client):
    from app.ai.agent import AgentReply
    from app.ai.llm_client import MockLLMClient

    monkeypatch.setattr(
        "app.api.dialog.build_llm_client", lambda settings: MockLLMClient()
    )

    async def fake_run_agent(user_id, text, **kwargs):
        assert text == "хочу kia sorento"
        return AgentReply(
            session_id="s1",
            text="Какой бюджет?",
            options=["до 30к"],
            turn_count=1,
            criteria={"make": "Kia"},
        )

    monkeypatch.setattr("app.api.dialog.run_agent", fake_run_agent)
    resp = await client.post(
        "/api/dialog/message",
        json={"user_id": 1, "text": "хочу kia sorento"},
        headers=HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["text"] == "Какой бюджет?"
    assert body["options"] == ["до 30к"]
    assert body["finalize_ready"] is False


@pytest.mark.asyncio
async def test_message_llm_unavailable_503(monkeypatch, client):
    from app.ai.llm_client import LLMError

    def fake_build(settings):
        raise LLMError("no key")

    monkeypatch.setattr("app.api.dialog.build_llm_client", fake_build)
    resp = await client.post(
        "/api/dialog/message", json={"user_id": 1, "text": "х"}, headers=HEADERS
    )
    assert resp.status_code == 503


@pytest.mark.asyncio
async def test_reset_endpoint(fake_redis, client):
    fake_redis.data["dialog:77"] = new_state(77).model_dump_json()
    resp = await client.post("/api/dialog/reset", json={"user_id": 77}, headers=HEADERS)
    assert resp.status_code == 200
    assert resp.json() == {"reset": True}
    assert "dialog:77" not in fake_redis.data


@pytest.mark.asyncio
async def test_finalize_endpoint(monkeypatch, fake_redis, client):
    fake_redis.data["dialog:88"] = new_state(88).model_dump_json()

    async def fake_enqueue(session, criteria):
        return ["encar"]

    monkeypatch.setattr("app.api.dialog._enqueue_fetch", fake_enqueue)
    resp = await client.post(
        "/api/dialog/finalize",
        json={"user_id": 88, "criteria": {"make": "Kia", "price_max": 30000, "country": "KR"}},
        headers=HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["sources"] == ["encar"]
    assert body["criteria"]["make"] == "Kia"
    assert "dialog:88" not in fake_redis.data
