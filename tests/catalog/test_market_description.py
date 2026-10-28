"""Генерация описания лота (12 §2): текст на месте, лимит free 5/день.

Rate-limit — Redis INCR+EXPIRE по ключу `chat:rl:desc:{uid}` (FakeRedis
в тестах); 6-я генерация free-тарифа — 429 ai_generation_limit.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from httpx import AsyncClient

from app.infrastructure.db.models import User

pytestmark = pytest.mark.asyncio

# «Описание» от модели: 400-800 знаков по ТЗ, в тесте достаточно >40.
DESCRIPTION_TEXT = (
    "Kia Carnival 2021 года с пробегом 60 000 км — просторный семейный "
    "минивэн с дизельным мотором и автоматом. Один владелец по документам, "
    "обслуживание по регламенту. Осмотр и тест-драйв в удобное время."
)


class _FakeLLM:
    async def complete(self, messages):
        return SimpleNamespace(text=DESCRIPTION_TEXT, tool_calls=[])


@pytest.fixture
def fake_llm(monkeypatch):
    monkeypatch.setattr(
        "app.di.providers.adapters._llm", lambda: _FakeLLM()
    )


def _payload() -> dict:
    return {
        "make": "Kia",
        "model": "Carnival",
        "year": 2021,
        "price": 30000,
        "currency": "USD",
        "mileage_km": 60000,
        "fuel": "diesel",
        "transmission": "automatic",
        "photos_count": 4,
    }


async def test_description_generated(
    client: AsyncClient,
    auth_headers: dict,
    user: User,
    fake_llm,
):
    """POST /api/market/description → 200 {text} из LLM (12 §2)."""
    resp = await client.post(
        "/api/market/description", json=_payload(), headers=auth_headers
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"text": DESCRIPTION_TEXT}


async def test_description_limit_429_on_6th(
    client: AsyncClient,
    auth_headers: dict,
    fake_redis,
    user: User,
    fake_llm,
):
    """Free-тариф: 6-я генерация за день — 429 ai_generation_limit."""
    for _ in range(5):
        resp = await client.post(
            "/api/market/description", json=_payload(), headers=auth_headers
        )
        assert resp.status_code == 200, resp.text

    resp = await client.post(
        "/api/market/description", json=_payload(), headers=auth_headers
    )
    assert resp.status_code == 429
    assert "ai_generation_limit" in resp.json()["detail"]
    # Счётчик окна лежит под ключом chat:rl:desc:{uid} (12 §2).
    assert int(fake_redis.data[f"chat:rl:desc:{user.telegram_id}"]) == 6


async def test_description_paid_tier_skips_limit(
    client: AsyncClient, pro_user: User, fake_redis, fake_llm
):
    """Pro не ограничен дневным лимитом free-тарифа (12 §2)."""
    from app.infrastructure.security.tokens import create_access_token
    from app.config import get_settings

    token = create_access_token(pro_user.telegram_id, get_settings())
    headers = {"Authorization": f"Bearer {token}"}
    fake_redis.data[f"chat:rl:desc:{pro_user.telegram_id}"] = "5"

    resp = await client.post(
        "/api/market/description", json=_payload(), headers=headers
    )
    assert resp.status_code == 200, resp.text


async def test_description_guest_403(
    client: AsyncClient, guest_headers: dict, fake_llm
):
    """Гость — 403: генерация после входа (как размещение лотов)."""
    resp = await client.post(
        "/api/market/description", json=_payload(), headers=guest_headers
    )
    assert resp.status_code == 403
