"""Тесты /api/ai/* (AI-поиск Mini App, JWT).

Regression: пользователь берётся из user.telegram_id (PK таблицы users),
а не из несуществующего атрибута user.id (иначе AttributeError → 500).
"""

from types import SimpleNamespace

import pytest

from app.core.deps import get_current_user
from app.core.deps_internal import get_redis
from app.db.session import get_session
from app.main import app

from conftest import FakeRedis, FakeSession

TEST_USER = SimpleNamespace(
    telegram_id=111111, subscription_tier="free", subscription_until=None
)


@pytest.fixture
def client(fake_redis: FakeRedis):
    from httpx import ASGITransport, AsyncClient

    session = FakeSession()
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_current_user] = lambda: TEST_USER
    app.dependency_overrides[get_redis] = lambda: fake_redis
    yield (
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test"),
        session,
        fake_redis,
    )
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_ai_reset_uses_telegram_id(client):
    """Сброс диалога: 200 (а не 500) — user_id берётся из telegram_id."""
    http, _, _ = client
    resp = await http.post("/api/ai/reset")
    assert resp.status_code == 200
    assert resp.json() == {"reset": True}


@pytest.mark.asyncio
async def test_ai_message_uses_telegram_id(client, monkeypatch):
    """Ход диалога с моком LLM: 200 и ответ агента."""
    from app.api import ai_search as ai_search_mod
    from app.ai.llm_client import MockLLMClient

    monkeypatch.setattr(ai_search_mod, "build_llm_client", lambda s: MockLLMClient())
    http, session, _ = client
    resp = await http.post("/api/ai/message", json={"text": "Хочу BMW до 20000"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["text"] == "Ok."
    assert body["status"] == "collecting"


@pytest.mark.asyncio
async def test_ai_finalize_creates_filter_with_telegram_id(client):
    """Финализация: фильтр создаётся с user_id = telegram_id (FK users)."""
    http, session, _ = client
    resp = await http.post(
        "/api/ai/finalize",
        json={"criteria": {"make": "BMW", "price_max": 20000}},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["criteria"]["make"] == "BMW"
    assert body["sources"] == []  # FakeSession: нет включённых источников
    # UserFilter создан для правильного пользователя
    flt = next(o for o in session.added if o.__class__.__name__ == "UserFilter")
    assert flt.user_id == 111111
