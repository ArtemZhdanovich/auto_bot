"""Рынок в ленте объявлений (06 §4): source=market = owner-лоты + бейдж.

Сквозные тесты на тестовой БД (client-фикстура): чипс «🏪 Рынок» отдаёт
лоты с владельцем (owner_type IS NOT NULL, JOIN partner_offers убран),
у каждого — has_market=True; витрина партнёра (encar-лоты с оффером)
из рынка ленты исчезает. Обычный source — прежняя семантика.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.db.models import Listing, PartnerOffer
from tests.partners.helpers import create_partner

pytestmark = pytest.mark.asyncio


async def _make_listing(
    session: AsyncSession, *, source: str, source_id: str, make: str
) -> Listing:
    """Активное объявление заданного источника."""
    listing = Listing(
        source=source,
        source_id=source_id,
        url=f"https://example.test/{source}/{source_id}/",
        make=make,
        model="Carnival",
        year=2021,
        price=30000,
        currency="USD",
        mileage_km=60000,
        parsed_at=datetime.now(UTC),
    )
    session.add(listing)
    await session.flush()
    return listing


async def _owner_lot(
    session: AsyncSession, *, source_id: str, owner_user_id: int, make: str = "Kia"
) -> Listing:
    """Активный owner-лот рынка (user): попадает в чипс source=market."""
    listing = Listing(
        source="market",
        source_id=source_id,
        url=f"/listing/market/{source_id}",
        make=make,
        model="Sorento",
        year=2022,
        price=25000,
        currency="USD",
        mileage_km=30000,
        owner_type="user",
        owner_user_id=owner_user_id,
        parsed_at=datetime.now(UTC),
    )
    session.add(listing)
    await session.flush()
    return listing


async def test_market_feed_returns_only_owner_lots(
    client: AsyncClient, session: AsyncSession, listing: Listing, user
):
    """source=market: только owner-лоты; encar-лот из витрины — не рынок.

    Оффер партнёра на внешний (encar/kcar) лот больше не выводит лот в
    чипс рынка (06 §4): рынок = лоты с владельцем, витрина остаётся
    кабинетом партнёра.
    """
    other = await _make_listing(session, source="encar", source_id="mk-2", make="BMW")
    owner = await _owner_lot(
        session, source_id="own-1", owner_user_id=user.telegram_id
    )
    account, _ = await create_partner(
        session,
        name="Витрина",
        contact=None,
        plan="pro",
        daily_quota=50,
        balance=Decimal("100"),
        cpl_rate=None,
    )
    # Оффер на внешний kcar-лот: в витрине — да, в рынке ленты — нет.
    vitrine = await _make_listing(session, source="kcar", source_id="mk-3", make="Kia")
    session.add(
        PartnerOffer(partner_id=account.id, listing_id=vitrine.id, note="торг")
    )
    await session.flush()

    resp = await client.get("/api/listings", params={"source": "market"})
    assert resp.status_code == 200
    body = resp.json()
    ids = {item["id"] for item in body["items"]}
    # Только owner-лот (источник owner-лота — 'market')
    assert ids == {owner.id}
    assert body["items"][0]["has_market"] is True
    assert body["items"][0]["owner_type"] == "user"
    assert body["total"] == 1
    # Не попали: encar без оффера, внешние лоты из витрины партнёра
    assert listing.id not in ids and other.id not in ids
    assert vitrine.id not in ids


async def test_default_feed_badge_is_owner_based(
    client: AsyncClient, session: AsyncSession, listing: Listing
):
    """Без source: все encar/kcar; has_market owner-based — оффер на внешний
    лот бейдж «🏪 Рынок» больше не даёт (06 §4)."""
    other = await _make_listing(session, source="encar", source_id="mk-22", make="BMW")
    account, _ = await create_partner(
        session,
        name="Витрина-2",
        contact=None,
        plan="pro",
        daily_quota=50,
        balance=Decimal("100"),
        cpl_rate=None,
    )
    session.add(PartnerOffer(partner_id=account.id, listing_id=listing.id))
    await session.flush()

    body = (await client.get("/api/listings", params={"limit": 50})).json()
    by_id = {item["id"]: item for item in body["items"]}
    assert set(by_id) >= {listing.id, other.id}
    # Бейдж — только у owner-лотов; во внешних лотах владельца нет.
    assert by_id[listing.id]["has_market"] is False
    assert by_id[other.id]["has_market"] is False


async def test_explicit_source_still_filters_by_listings_source(
    client: AsyncClient, session: AsyncSession, listing: Listing
):
    """Обычный source — прежняя семантика: фильтр по listings.source."""
    kcar = await _make_listing(session, source="kcar", source_id="mk-31", make="Kia")
    body = (await client.get("/api/listings", params={"source": "kcar"})).json()
    ids = {item["id"] for item in body["items"]}
    assert kcar.id in ids and listing.id not in ids


async def test_market_feed_is_empty_without_owner_lots(
    client: AsyncClient, session: AsyncSession, listing: Listing
):
    """Owner-лотов нет (витрина не считается) — чипс рынка пуст, не 500."""
    account, _ = await create_partner(
        session,
        name="Витрина-3",
        contact=None,
        plan="pro",
        daily_quota=50,
        balance=Decimal("100"),
        cpl_rate=None,
    )
    session.add(PartnerOffer(partner_id=account.id, listing_id=listing.id))
    await session.flush()

    resp = await client.get("/api/listings", params={"source": "market"})
    assert resp.status_code == 200
    assert resp.json()["items"] == [] and resp.json()["total"] == 0
