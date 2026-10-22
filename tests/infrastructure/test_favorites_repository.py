"""Интеграционные тесты репозитория избранного и UoW/outbox."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.infrastructure.db.models import Favorite, Listing, User
from app.domain.favorites.ports import FavoriteCursor
from app.domain.shared.events import DomainEvent
from app.infrastructure.db.outbox import OutboxDAO, OutboxMessage
from app.infrastructure.db.repositories.favorites import SqlAlchemyFavoriteRepository
from app.infrastructure.db.uow import SqlAlchemyUnitOfWork

BASE_TIME = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


async def _make_user(session, telegram_id: int = 910001) -> User:
    user = User(
        telegram_id=telegram_id,
        role="buyer",
        subscription_tier="free",
        is_active=True,
        created_at=BASE_TIME,
    )
    session.add(user)
    await session.flush()
    return user


async def _make_listing(session, source_id: str) -> Listing:
    listing = Listing(
        source="test",
        source_id=source_id,
        url=f"https://example.com/{source_id}",
    )
    session.add(listing)
    await session.flush()
    return listing


@pytest.mark.asyncio
async def test_toggle_adds_then_removes(session) -> None:
    user = await _make_user(session)
    listing = await _make_listing(session, "toggle-1")
    repo = SqlAlchemyFavoriteRepository(session)

    assert await repo.toggle(user.telegram_id, listing.id) is True
    assert await repo.toggle(user.telegram_id, listing.id) is False

    rows = (
        await session.execute(
            select(Favorite).where(Favorite.user_id == user.telegram_id)
        )
    ).scalars().all()
    assert rows == []


@pytest.mark.asyncio
async def test_page_uses_keyset_and_returns_first_price(session) -> None:
    user = await _make_user(session, telegram_id=910002)
    listings = [await _make_listing(session, f"page-{index}") for index in range(3)]

    for index, listing in enumerate(listings):
        session.add(
            Favorite(
                user_id=user.telegram_id,
                listing_id=listing.id,
                created_at=BASE_TIME + timedelta(minutes=index),
            )
        )
    await session.flush()

    repo = SqlAlchemyFavoriteRepository(session)
    first_page, has_more = await repo.page(user.telegram_id, limit=2, cursor=None)

    assert has_more is True
    assert [item.listing_id for item in first_page] == [
        listings[2].id,
        listings[1].id,
    ]

    last = first_page[-1]
    second_page, has_more_second = await repo.page(
        user.telegram_id,
        limit=2,
        cursor=FavoriteCursor(created_at=last.created_at, listing_id=last.listing_id),
    )

    assert has_more_second is False
    assert [item.listing_id for item in second_page] == [listings[0].id]


@pytest.mark.asyncio
async def test_uow_writes_event_to_outbox_on_commit(session) -> None:
    uow = SqlAlchemyUnitOfWork(
        session, OutboxDAO(session), publisher=None, use_outbox=True
    )
    event = DomainEvent()

    async with uow:
        uow.collect(event)
        await uow.commit()

    rows = (await session.execute(select(OutboxMessage))).scalars().all()
    assert len(rows) == 1
    assert rows[0].event_id == event.event_id
    assert rows[0].published_at is None


@pytest.mark.asyncio
async def test_uow_rollback_discards_outbox(session) -> None:
    uow = SqlAlchemyUnitOfWork(
        session, OutboxDAO(session), publisher=None, use_outbox=True
    )
    event = DomainEvent()

    with pytest.raises(RuntimeError):
        async with uow:
            uow.collect(event)
            raise RuntimeError("boom")

    rows = (await session.execute(select(OutboxMessage))).scalars().all()
    assert rows == []
