"""Изолированные тесты use case избранного — без БД и фреймворков."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.application.favorites.use_cases import (
    ListFavorites,
    ListFavoritesQuery,
    ToggleFavorite,
    ToggleFavoriteCommand,
)
from app.application.shared.pagination import CursorCodec, Page
from app.domain.favorites.ports import FavoriteCursor, FavoriteItem
from app.application.errors import NotFound

BASE_TIME = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


class FakeUnitOfWork:
    """UoW-заглушка: фиксирует commit/rollback и события входа."""

    def __init__(self) -> None:
        self.committed = False
        self.rolled_back = False
        self.entered = False

    async def __aenter__(self) -> "FakeUnitOfWork":
        self.entered = True
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if exc_type is not None:
            self.rolled_back = True

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        self.rolled_back = True


class FakeListingRepository:
    """Активные объявления — множество id."""

    def __init__(self, active: set[int]) -> None:
        self.active = set(active)

    async def exists_active(self, listing_id: int) -> bool:
        return listing_id in self.active


class FakeFavoriteRepository:
    """Избранное в памяти с семантикой toggle и keyset-страницы."""

    def __init__(self) -> None:
        self._items: dict[tuple[int, int], FavoriteItem] = {}
        self.last_limit: int | None = None

    async def toggle(self, user_id: int, listing_id: int) -> bool:
        key = (user_id, listing_id)
        if key in self._items:
            del self._items[key]
            return False
        self._items[key] = FavoriteItem(
            listing_id=listing_id, created_at=BASE_TIME, first_price=None
        )
        return True

    async def page(
        self,
        user_id: int,
        *,
        limit: int,
        cursor: FavoriteCursor | None,
        offset: int = 0,
    ) -> tuple[list[FavoriteItem], bool]:
        self.last_limit = limit
        rows = [
            item
            for (uid, _lid), item in sorted(
                self._items.items(),
                key=lambda pair: (pair[1].created_at, pair[1].listing_id),
                reverse=True,
            )
            if uid == user_id
        ]
        if cursor is not None:
            rows = [
                item
                for item in rows
                if (item.created_at, item.listing_id)
                < (cursor.created_at, cursor.listing_id)
            ]
        elif offset:
            rows = rows[offset:]
        return rows[:limit], len(rows) > limit


def _toggle_case(active: set[int]) -> tuple[ToggleFavorite, FakeUnitOfWork, FakeFavoriteRepository]:
    uow = FakeUnitOfWork()
    favorites = FakeFavoriteRepository()
    use_case = ToggleFavorite(uow, FakeListingRepository(active), favorites)
    return use_case, uow, favorites


@pytest.mark.asyncio
async def test_toggle_adds_and_commits() -> None:
    use_case, uow, _ = _toggle_case({10})

    state = await use_case.execute(ToggleFavoriteCommand(user_id=1, listing_id=10))

    assert state.favorited is True
    assert uow.committed is True
    assert uow.rolled_back is False


@pytest.mark.asyncio
async def test_second_toggle_removes() -> None:
    use_case, _, _ = _toggle_case({10})

    first = await use_case.execute(ToggleFavoriteCommand(user_id=1, listing_id=10))
    second = await use_case.execute(ToggleFavoriteCommand(user_id=1, listing_id=10))

    assert (first.favorited, second.favorited) == (True, False)


@pytest.mark.asyncio
async def test_missing_listing_raises_and_does_not_commit() -> None:
    use_case, uow, favorites = _toggle_case(set())

    with pytest.raises(NotFound) as exc_info:
        await use_case.execute(ToggleFavoriteCommand(user_id=1, listing_id=99))

    assert "Listing not found" in str(exc_info.value)
    assert uow.committed is False
    assert favorites.last_limit is None


@pytest.mark.asyncio
async def test_list_returns_next_cursor() -> None:
    favorites = FakeFavoriteRepository()
    for index in range(3):
        favorites._items[(1, index + 1)] = FavoriteItem(
            listing_id=index + 1,
            created_at=BASE_TIME + timedelta(minutes=index),
            first_price="1000",
        )

    page = await ListFavorites(FakeUnitOfWork(), favorites).execute(
        ListFavoritesQuery(user_id=1, limit=2)
    )

    assert isinstance(page, Page)
    assert len(page.items) == 2
    assert page.has_more is True
    assert page.next_cursor is not None

    cursor = CursorCodec.decode_favorite(page.next_cursor)
    assert (cursor.created_at, cursor.listing_id) == (
        page.items[-1].created_at,
        page.items[-1].listing_id,
    )


@pytest.mark.asyncio
async def test_limit_is_clamped_to_max() -> None:
    favorites = FakeFavoriteRepository()

    await ListFavorites(FakeUnitOfWork(), favorites).execute(
        ListFavoritesQuery(user_id=1, limit=10_000)
    )

    assert favorites.last_limit == 100


def test_cursor_rejects_garbage() -> None:
    with pytest.raises(ValueError, match="Invalid cursor"):
        CursorCodec.decode_favorite("not-a-cursor")
