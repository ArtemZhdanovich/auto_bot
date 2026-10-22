"""Флаг `use_case_favorites`: список избранного через use case (фаза 3)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.application.shared.pagination import CursorCodec
from app.config import get_settings
from app.infrastructure.db.models import Favorite, Listing, ListingPriceHistory

_BASE = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


async def _make_listing(session, source_id: str, price: float) -> Listing:
    listing = Listing(
        source="test",
        source_id=source_id,
        url=f"https://example.com/{source_id}",
        make="Kia",
        model="Carnival",
        year=2021,
        price=price,
        currency="USD",
        parsed_at=_BASE,
    )
    session.add(listing)
    await session.flush()
    return listing


async def _add_favorite(session, user, listing: Listing, created_at: datetime) -> None:
    session.add(
        Favorite(
            user_id=user.telegram_id,
            listing_id=listing.id,
            created_at=created_at,
        )
    )
    await session.flush()


@pytest.mark.asyncio
async def test_list_via_use_case_limit_and_cursor(
    client, session, user, auth_headers_user, monkeypatch
):
    """Флаг включён: `limit` режет страницу, порядок новые сверху, курсор листает."""
    monkeypatch.setattr(get_settings(), "use_case_favorites", True)

    oldest = await _make_listing(session, "fav-oldest", 10000)
    middle = await _make_listing(session, "fav-middle", 20000)
    newest = await _make_listing(session, "fav-newest", 30000)

    await _add_favorite(session, user, oldest, _BASE)
    await _add_favorite(session, user, middle, _BASE + timedelta(hours=1))
    await _add_favorite(session, user, newest, _BASE + timedelta(hours=2))

    # Бейдж «цена снизилась»: first_price приходит из FavoriteItem.
    session.add(
        ListingPriceHistory(
            listing_id=newest.id,
            price=Decimal("1500000.00"),
            recorded_at=_BASE + timedelta(hours=2),
        )
    )
    await session.flush()

    first = await client.get("/api/favorites?limit=2", headers=auth_headers_user)
    assert first.status_code == 200
    page = first.json()
    assert isinstance(page, list)
    assert [item["id"] for item in page] == [newest.id, middle.id]
    assert page[0]["first_price"] == 1500000.0
    assert set(page[0]) >= {"id", "first_price"}

    # Курсор собираем сами из created_at/listing_id последнего элемента страницы.
    last_favorite = (
        await session.execute(
            select(Favorite).where(
                Favorite.user_id == user.telegram_id,
                Favorite.listing_id == middle.id,
            )
        )
    ).scalar_one()
    cursor = CursorCodec.encode(
        {
            "created_at": last_favorite.created_at.isoformat(),
            "listing_id": last_favorite.listing_id,
        }
    )

    second = await client.get(
        f"/api/favorites?limit=2&cursor={cursor}", headers=auth_headers_user
    )
    assert second.status_code == 200
    assert [item["id"] for item in second.json()] == [oldest.id]

    # Битый курсор — 400, а не 500: контракт ошибок явный.
    broken = await client.get(
        "/api/favorites?cursor=not-a-cursor", headers=auth_headers_user
    )
    assert broken.status_code == 400


@pytest.mark.asyncio
async def test_list_flag_on_offset_falls_back_to_offset_query(
    client, session, user, auth_headers_user, monkeypatch
):
    """Флаг включён, offset > 0: работает прежний LIMIT/OFFSET-запрос."""
    monkeypatch.setattr(get_settings(), "use_case_favorites", True)

    oldest = await _make_listing(session, "off-oldest", 10000)
    middle = await _make_listing(session, "off-middle", 20000)
    newest = await _make_listing(session, "off-newest", 30000)

    await _add_favorite(session, user, oldest, _BASE)
    await _add_favorite(session, user, middle, _BASE + timedelta(hours=1))
    await _add_favorite(session, user, newest, _BASE + timedelta(hours=2))

    response = await client.get(
        "/api/favorites?limit=2&offset=1", headers=auth_headers_user
    )
    assert response.status_code == 200
    assert [item["id"] for item in response.json()] == [middle.id, oldest.id]
