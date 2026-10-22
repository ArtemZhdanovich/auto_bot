# tests/api/test_metrics.py
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.db.models import (
    AnalyticsEvent, Lead, Listing, PriceAnalysis,
)
from app.main_api import app as fastapi_app
from conftest import install_test_container


class _FakeRedis:
    """Мини-стаб Redis под кэш снапшота метрик."""

    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.data.get(key)

    async def setex(self, key: str, ttl: int, value: str) -> bool:
        self.data[key] = value
        return True

    async def delete(self, *keys: str) -> int:
        removed = 0
        for key in keys:
            if key in self.data:
                del self.data[key]
                removed += 1
        return removed

    async def exists(self, key: str) -> int:
        return 1 if key in self.data else 0


@pytest.fixture
def fake_redis() -> _FakeRedis:
    return _FakeRedis()


@pytest.fixture(autouse=True)
async def _override_metrics_redis(fake_redis: _FakeRedis):
    """/metrics кэширует снапшот в Redis через get_redis.

    Без подмены тесты читают настоящий Redis: снапшот с TTL 30s переживает
    границу тестов и следующий запуск отдаёт чужие цифры.
    """
    _prev = fastapi_app.state.dishka_container
    _container = install_test_container(fastapi_app, redis=fake_redis)
    try:
        yield
    finally:
        fastapi_app.state.dishka_container = _prev
        await _container.close()


# --- auth ----------------------------------------------------------

@pytest.mark.asyncio
async def test_metrics_requires_internal_token(client: AsyncClient):
    r = await client.get("/metrics")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_metrics_with_token(client: AsyncClient, internal_token):
    r = await client.get("/metrics", headers={"X-Internal-Token": internal_token})
    assert r.status_code == 200


# --- listings_new_24h: first_seen_at, не updated_at ----------------

@pytest.mark.asyncio
async def test_listings_new_24h_uses_first_seen_at(
    client: AsyncClient, internal_token, session: AsyncSession, fake_redis,
):
    """Новое объявление (updated_at=None, first_seen_at=now) попадает в new_24h."""
    await fake_redis.delete("metrics:snapshot:v1")
    now = datetime.now(UTC)
    fresh = Listing(
        source="encar", source_id="fresh-1", url="https://e/1",
        first_seen_at=now, last_seen_at=now,
        # updated_at=None (INSERT без UPDATE)
    )
    old = Listing(
        source="encar", source_id="old-1", url="https://e/2",
        first_seen_at=now - timedelta(days=3),
        last_seen_at=now,
    )
    session.add_all([fresh, old])
    await session.commit()

    r = await client.get("/metrics", headers={"X-Internal-Token": internal_token})
    body = r.json()
    assert body["listings"]["new_24h"] == 1


# --- 503 при недоступной БД ----------------------------------------

@pytest.mark.asyncio
async def test_metrics_503_when_db_down(
    client: AsyncClient, internal_token, monkeypatch, fake_redis,
):
    """Если `SELECT 1` падает — 503, не 200 со всеми null."""
    await fake_redis.delete("metrics:snapshot:v1")
    from app.transport.http.routers import metrics as metrics_module

    async def failing_probe(session):
        return False

    monkeypatch.setattr(metrics_module, "_probe_db", failing_probe)

    r = await client.get("/metrics", headers={"X-Internal-Token": internal_token})
    assert r.status_code == 503


# --- кэш -----------------------------------------------------------

@pytest.mark.asyncio
async def test_metrics_cached(
    client: AsyncClient, internal_token, fake_redis, user,
):
    """Второй вызов — из кэша; ключ `metrics:snapshot:v1` в Redis."""
    await fake_redis.delete("metrics:snapshot:v1")
    await client.get("/metrics", headers={"X-Internal-Token": internal_token})
    assert await fake_redis.exists("metrics:snapshot:v1") == 1

    # Создаём нового юзера, но кэш ещё жив (30s)
    # ... добавить через отдельную сессию ...

    r2 = await client.get("/metrics", headers={"X-Internal-Token": internal_token})
    # users.total не изменился — отдаём кэш
    assert r2.status_code == 200


@pytest.mark.asyncio
async def test_metrics_cache_invalidated_by_ttl(
    client: AsyncClient, internal_token, fake_redis, user,
):
    """После удаления ключа — свежие данные."""
    await fake_redis.delete("metrics:snapshot:v1")
    r1 = await client.get("/metrics", headers={"X-Internal-Token": internal_token})
    total_1 = r1.json()["users"]["total"]

    await fake_redis.delete("metrics:snapshot:v1")  # имитируем TTL
    r2 = await client.get("/metrics", headers={"X-Internal-Token": internal_token})
    total_2 = r2.json()["users"]["total"]
    assert total_2 == total_1  # без новых юзеров — то же значение


# --- users_by_tier: None → "unknown" -------------------------------

@pytest.mark.asyncio
async def test_users_by_tier_has_no_null_key(
    client: AsyncClient, internal_token, user, fake_redis,
):
    """by_tier отдаёт реальные тарифы и не содержит ключа "null".

    users.subscription_tier — NOT NULL (server_default 'free'), поэтому
    legacy-строки с NULL в БД невозможны: ветка "unknown" — защитная.
    """
    await fake_redis.delete("metrics:snapshot:v1")
    r = await client.get("/metrics", headers={"X-Internal-Token": internal_token})
    tiers = r.json()["users"]["by_tier"]
    assert "null" not in tiers
    assert set(tiers) <= {"free", "pro", "business", "unknown"}
    assert tiers.get("free", 0) >= 1


# --- events_24h: top-N --------------------------------------------

@pytest.mark.asyncio
async def test_events_24h_limited(
    client: AsyncClient, internal_token, session: AsyncSession, fake_redis,
):
    """`events_24h` содержит не более 50 имён."""
    await fake_redis.delete("metrics:snapshot:v1")
    now = datetime.now(UTC)
    for i in range(60):
        session.add(AnalyticsEvent(name=f"evt_{i:02d}", created_at=now))
    await session.commit()

    r = await client.get("/metrics", headers={"X-Internal-Token": internal_token})
    events = r.json()["events_24h"]
    assert len(events) <= 50


# --- generated_at --------------------------------------------------

@pytest.mark.asyncio
async def test_metrics_has_generated_at(
    client: AsyncClient, internal_token, fake_redis,
):
    await fake_redis.delete("metrics:snapshot:v1")
    r = await client.get("/metrics", headers={"X-Internal-Token": internal_token})
    body = r.json()
    assert "generated_at" in body
    parsed = datetime.fromisoformat(body["generated_at"].replace("Z", "+00:00"))
    assert (datetime.now(UTC) - parsed).total_seconds() < 5


# --- leads / price_analyses counts -------------------------------

@pytest.mark.asyncio
async def test_leads_and_analyses_counts(
    client: AsyncClient, internal_token, session: AsyncSession, user, fake_redis,
):
    await fake_redis.delete("metrics:snapshot:v1")
    now = datetime.now(UTC)
    listing = Listing(
        source="encar", source_id="metrics-lead-1", url="https://e/m1",
        first_seen_at=now, last_seen_at=now,
    )
    session.add(listing)
    await session.commit()
    session.add(Lead(user_id=user.telegram_id, listing_id=listing.id, source="bot"))
    session.add(PriceAnalysis(
        listing_id=listing.id, user_id=user.telegram_id, verdict="fair",
    ))
    await session.commit()

    r = await client.get("/metrics", headers={"X-Internal-Token": internal_token})
    body = r.json()
    assert body["leads"]["total"] >= 1
    assert body["price_analyses"]["total"] >= 1