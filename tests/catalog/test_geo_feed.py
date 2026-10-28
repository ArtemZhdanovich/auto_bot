"""Гео-контур ленты и фасетов (GEO-V1 §2): фильтр `?country=` + countries.

Сквозные API-тесты на тестовой БД (client-фикстура, как test_listings.py):
фильтр ленты по ISO-2 (в т.ч. нижний регистр), обход MVP-дефолта
«только Корея» при явной стране, поля country/city в ListingOut,
фасет `countries` по частоте и кэш-ключ `facets:v2:…`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.db.models import Listing

pytestmark = pytest.mark.asyncio


async def _seed(
    session: AsyncSession,
    *,
    source: str,
    source_id: str,
    country: str | None,
    make: str = "Kia",
    is_active: bool = True,
) -> Listing:
    """Активное объявление источника со страной (GEO-V1 §2)."""
    listing = Listing(
        source=source,
        source_id=source_id,
        url=f"https://{source}.test/{source_id}",
        make=make,
        model="Carnival",
        year=2021,
        price=Decimal("20000"),
        currency="USD",
        country=country,
        is_active=is_active,
        parsed_at=datetime.now(UTC),
    )
    session.add(listing)
    await session.commit()
    await session.refresh(listing)
    return listing


# =========================================================================
# Фильтр ленты ?country=
# =========================================================================


async def test_feed_filters_by_country_exact(client, session):
    """?country=KR — только корейские лоты; страна в каждой карточке."""
    await _seed(session, source="encar", source_id="g-kr-1", country="KR")
    await _seed(session, source="kcar", source_id="g-kr-2", country="KR")
    await _seed(session, source="che168", source_id="g-cn-1", country="CN")

    resp = await client.get("/api/listings", params={"country": "KR"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    assert {item["source"] for item in body["items"]} == {"encar", "kcar"}
    # ListingOut.country проставлен из модели (бейдж-флаг на карточке).
    assert all(item["country"] == "KR" for item in body["items"])


async def test_feed_country_lowercase_normalized(client, session):
    """?country=kr — нижний регистр сравнивается с колонкой (upper)."""
    await _seed(session, source="encar", source_id="g-lc-1", country="KR")
    resp = await client.get("/api/listings", params={"country": "kr"})
    assert resp.status_code == 200
    assert resp.json()["total"] == 1


async def test_feed_country_extends_mvp_korea_default(client, session):
    """Явная страна расширяет MVP-дефолт «только encar/kcar».

    Без source лента по умолчанию показывает лишь Корею; чипс «Китай»
    (country=CN) не должен глохнуть на этом дефолте — отдаём che168.
    """
    await _seed(session, source="che168", source_id="g-cn-2", country="CN")
    resp = await client.get("/api/listings", params={"country": "CN"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["source"] == "che168"
    assert body["items"][0]["country"] == "CN"


async def test_feed_country_validation_422(client, session):
    """country=USA (не ISO-2) → 422."""
    resp = await client.get("/api/listings", params={"country": "USA"})
    assert resp.status_code == 422


async def test_feed_country_unknown_is_empty_not_error(client, session):
    """Страна без лотов — пустая страница, не ошибка."""
    await _seed(session, source="encar", source_id="g-ae-1", country="KR")
    resp = await client.get("/api/listings", params={"country": "AE"})
    assert resp.status_code == 200
    assert resp.json()["total"] == 0


# =========================================================================
# Фасеты: ключ countries + кэш facets:v2
# =========================================================================


async def test_facets_countries_by_frequency(client, session):
    """facets → countries: distinct по активным, частые первыми."""
    await _seed(session, source="encar", source_id="fc-kr-1", country="KR")
    await _seed(session, source="encar", source_id="fc-kr-2", country="KR")
    await _seed(session, source="che168", source_id="fc-cn-1", country="CN")
    # Неактивный US-лот в фасете не участвует.
    await _seed(
        session, source="cars_com", source_id="fc-us-1",
        country="US", is_active=False,
    )

    resp = await client.get("/api/listings/facets")
    assert resp.status_code == 200
    body = resp.json()
    assert body["countries"] == ["KR", "CN"]
    assert body["makes"]  # прежний контракт на месте


async def test_facets_country_scopes_makes(client, session):
    """facets?country=KR — марки сужаются до страны."""
    await _seed(
        session, source="encar", source_id="fs-kr-1",
        country="KR", make="Kia",
    )
    await _seed(
        session, source="che168", source_id="fs-cn-1",
        country="CN", make="Chery",
    )
    resp = await client.get("/api/listings/facets", params={"country": "KR"})
    assert resp.status_code == 200
    makes = resp.json()["makes"]
    assert "Kia" in makes
    assert "Chery" not in makes


async def test_facets_cache_key_is_v2(client, session, fake_redis):
    """Кэш фасетов переехал на ключ facets:v2 (форма ответа изменилась)."""
    await _seed(session, source="encar", source_id="fk-1", country="KR")
    resp = await client.get("/api/listings/facets")
    assert resp.status_code == 200
    assert "facets:v2:makes" in fake_redis.data
    # Старого ключа больше не пишем — кэш не отдаёт форму без countries.
    assert "facets:makes" not in fake_redis.data
