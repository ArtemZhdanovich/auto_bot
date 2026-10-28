"""Гео рынка (GEO-V1 §2): country/city в лоте, MyLotOut, витрина /lots.

Сквозные API-тесты на тестовой БД (client-фикстура, как
test_market_lots.py): страна обязательна при создании owner-лота и
нормализуется в верхний регистр, город опционален, PATCH правит гео,
GET /api/market/lots?country= фильтрует витрину по ISO-2.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.db.models import Listing, User

pytestmark = pytest.mark.asyncio

LOT_BODY = {
    "make": "Hyundai",
    "model": "Palisade",
    "year": 2022,
    "price": 38000,
    "currency": "USD",
    "mileage_km": 42000,
    "description": "Один владелец, сервис по регламенту",
    "photos": ["https://cdn.example.test/1.jpg"],
    "country": "RU",
}


async def _seed_history(session: AsyncSession, user_id: int) -> None:
    """Исторический accepted-лот: снимает правило new_user (10 §2)."""
    stale = datetime.now(UTC) - timedelta(days=40)
    session.add(
        Listing(
            source="market",
            source_id=f"geo-hist-{user_id}",
            url="/listing/geo-hist",
            make="Oldsmobile",
            model="Ride",
            year=2010,
            price=Decimal("1000"),
            currency="USD",
            owner_type="user",
            owner_user_id=user_id,
            is_active=False,
            status="accepted",
            placement_free_until=stale,
            first_seen_at=stale,
            parsed_at=stale,
        )
    )
    await session.flush()


async def _create_lot(client, headers: dict, **overrides) -> dict:
    """POST /api/market/lots (submit=true) → тело ответа."""
    resp = await client.post(
        "/api/market/lots",
        json={**LOT_BODY, "submit": True, **overrides},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _get_row(session: AsyncSession, listing_id: int) -> Listing:
    return await session.get(Listing, listing_id)


# =========================================================================
# Создание: страна обязательна, город опционален
# =========================================================================


async def test_create_lot_requires_country(client, auth_headers):
    """POST без country → 422 (GEO-V1 §2: обязателен для owner-лотов)."""
    body = {k: v for k, v in LOT_BODY.items() if k != "country"}
    resp = await client.post(
        "/api/market/lots", json={**body, "submit": True}, headers=auth_headers
    )
    assert resp.status_code == 422


async def test_create_lot_country_pattern(client, auth_headers):
    """country=USA (не ISO-2) → 422."""
    resp = await client.post(
        "/api/market/lots",
        json={**LOT_BODY, "country": "USA"},
        headers=auth_headers,
    )
    assert resp.status_code == 422


async def test_create_lot_saves_country_and_city(
    client, auth_headers, session, user: User
):
    """Страна нормализуется («ru» → RU), город пишется как есть."""
    await _seed_history(session, user.telegram_id)
    created = await _create_lot(client, auth_headers, country="ru", city="Казань")

    row = await _get_row(session, created["listing_id"])
    assert row.country == "RU"
    assert row.city == "Казань"


async def test_create_lot_city_optional(client, auth_headers, session, user):
    """Без города — country есть, city NULL."""
    await _seed_history(session, user.telegram_id)
    created = await _create_lot(client, auth_headers)

    row = await _get_row(session, created["listing_id"])
    assert row.country == "RU"
    assert row.city is None


async def test_my_lots_carry_country_city(
    client, auth_headers, session, user
):
    """MyLotOut несёт country/city (карточка «🇷🇺 RU · Москва»)."""
    await _seed_history(session, user.telegram_id)
    created = await _create_lot(client, auth_headers, city="Москва")

    resp = await client.get("/api/market/my-lots", headers=auth_headers)
    assert resp.status_code == 200
    mine = next(l for l in resp.json() if l["listing_id"] == created["listing_id"])
    assert mine["country"] == "RU"
    assert mine["city"] == "Москва"


# =========================================================================
# PATCH: правка гео
# =========================================================================


async def test_patch_updates_city(client, auth_headers, session, user):
    """PATCH city/country применяются к черновику напрямую."""
    created = await _create_lot(client, auth_headers, submit=False)

    resp = await client.patch(
        f"/api/market/lots/{created['listing_id']}",
        json={"city": "Сочи", "country": "KZ"},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["country"] == "KZ"
    assert body["city"] == "Сочи"

    row = await _get_row(session, created["listing_id"])
    assert row.country == "KZ"
    assert row.city == "Сочи"


# =========================================================================
# Витрина GET /api/market/lots?country=
# =========================================================================


async def _seed_market_lot(
    session: AsyncSession,
    user_id: int,
    *,
    source_id: str,
    country: str | None,
    city: str | None = None,
) -> int:
    """Опубликованный owner-лот с гео (витрина видит только такие)."""
    stale = datetime.now(UTC) - timedelta(days=40)
    lot = Listing(
        source="market",
        source_id=source_id,
        url=f"/listing/geo-{source_id}",
        make="Hyundai",
        model="Palisade",
        year=2022,
        price=Decimal("38000"),
        currency="USD",
        owner_type="user",
        owner_user_id=user_id,
        is_active=True,
        status="accepted",
        country=country,
        city=city,
        first_seen_at=stale,
        parsed_at=stale,
    )
    session.add(lot)
    await session.flush()
    return lot.id


async def test_market_lots_country_filter(client, auth_headers, session, user):
    """?country=RU — только RU-лоты; без фильтра — все опубликованные."""
    await _seed_market_lot(
        session, user.telegram_id, source_id="geo-v-ru", country="RU", city="Москва"
    )
    ru_id = await _seed_market_lot(
        session, user.telegram_id, source_id="geo-v-ru2", country="RU"
    )
    await _seed_market_lot(session, user.telegram_id, source_id="geo-v-kz", country="KZ")

    resp = await client.get("/api/market/lots", params={"country": "RU"},
                            headers=auth_headers)
    assert resp.status_code == 200
    lots = resp.json()
    assert {lot["listing_id"] for lot in lots} >= {ru_id}
    assert all(lot["country"] == "RU" for lot in lots)
    assert any(lot["city"] == "Москва" for lot in lots)

    resp_all = await client.get("/api/market/lots", headers=auth_headers)
    countries = {lot["country"] for lot in resp_all.json()}
    assert {"RU", "KZ"} <= countries


async def test_market_lots_skip_unpublished(client, auth_headers, session, user):
    """Черновики и отклонённые в витрине не видны (только accepted)."""
    await _seed_market_lot(session, user.telegram_id, source_id="geo-v-ok", country="RU")
    stale = datetime.now(UTC) - timedelta(days=40)
    session.add(
        Listing(
            source="market",
            source_id="geo-v-draft",
            url="/listing/geo-v-draft",
            make="Kia",
            model="K5",
            year=2020,
            price=Decimal("15000"),
            currency="USD",
            owner_type="user",
            owner_user_id=user.telegram_id,
            is_active=False,
            status="draft",
            country="RU",
            first_seen_at=stale,
            parsed_at=stale,
        )
    )
    await session.flush()

    resp = await client.get("/api/market/lots", params={"country": "RU"},
                            headers=auth_headers)
    assert resp.status_code == 200
    assert all(lot["status"] == "accepted" for lot in resp.json())
