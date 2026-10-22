"""Сценарии работы с избранным.

Ни один сценарий не знает про FastAPI, SQLAlchemy или RabbitMQ: только
порты и доменные ошибки.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.application.shared.dto import FavoriteState
from app.application.shared.pagination import CursorCodec, Page
from app.application.shared.ports import UnitOfWork
from app.domain.favorites.ports import FavoriteCursor, FavoriteItem, FavoriteRepository
from dataclasses import replace

from app.application.dto.listings import ListingCard
from app.application.errors import NotFound
from app.domain.listings.ports import ListingRepository

FAVORITES_MAX_LIMIT = 100


@dataclass(frozen=True, slots=True)
class ToggleFavoriteCommand:
    """Вход сценария toggle."""

    user_id: int
    listing_id: int


@dataclass(frozen=True, slots=True)
class ListFavoritesQuery:
    """Вход сценария списка избранного."""

    user_id: int
    limit: int = 50
    cursor: FavoriteCursor | None = None
    offset: int = 0


class ToggleFavorite:
    """Переключить избранное для текущего пользователя.

    Проверка существования объявления — здесь, а не в роутере: сценарий
    нельзя вызвать «забыв» проверку.
    """

    def __init__(
        self,
        uow: UnitOfWork,
        listings: ListingRepository,
        favorites: FavoriteRepository,
    ) -> None:
        self._uow = uow
        self._listings = listings
        self._favorites = favorites

    async def execute(self, command: ToggleFavoriteCommand) -> FavoriteState:
        """Выполнить toggle.

        Raises:
            ListingNotFound: Объявления нет или оно неактивно.
        """
        async with self._uow:
            if not await self._listings.exists_active(command.listing_id):
                raise NotFound("Listing not found")
            favorited = await self._favorites.toggle(
                command.user_id, command.listing_id
            )
            await self._uow.commit()
        return FavoriteState(favorited=favorited)


class IsFavorite:
    """Проверить, состоит ли объявление в избранном."""

    def __init__(
        self,
        uow: UnitOfWork,
        favorites: FavoriteRepository,
        listings: ListingRepository,
    ) -> None:
        self._uow = uow
        self._favorites = favorites
        self._listings = listings

    async def execute(self, user_id: int, listing_id: int) -> bool:
        """Вернуть `favorited`.

        Raises:
            ListingNotFound: Объявления нет или оно неактивно.
        """
        async with self._uow:
            if not await self._listings.exists_active(listing_id):
                raise NotFound("Listing not found")
            return await self._favorites.exists(user_id, listing_id)


class RemoveFavorite:
    """Идемпотентно удалить объявление из избранного."""

    def __init__(self, uow: UnitOfWork, favorites: FavoriteRepository) -> None:
        self._uow = uow
        self._favorites = favorites

    async def execute(self, user_id: int, listing_id: int) -> bool:
        """Вернуть `True`, если запись действительно была удалена."""
        async with self._uow:
            removed = await self._favorites.remove(user_id, listing_id)
            await self._uow.commit()
            return removed


class ListFavoriteCards:
    """Страница избранного с полными карточками объявлений.

    Соединяет порты избранного и объявлений; transport получает готовые
    поля и не делает SQL.
    """

    def __init__(
        self,
        uow: UnitOfWork,
        favorites: FavoriteRepository,
        listings: ListingRepository,
    ) -> None:
        self._uow = uow
        self._favorites = favorites
        self._listings = listings

    async def execute(self, query: ListFavoritesQuery) -> list[ListingCard]:
        """Вернуть карточки в порядке избранного."""
        limit = max(1, min(query.limit, FAVORITES_MAX_LIMIT))
        async with self._uow:
            items, _has_more = await self._favorites.page(
                query.user_id,
                limit=limit,
                cursor=query.cursor,
                offset=max(0, query.offset),
            )
            cards = await self._listings.cards_by_ids(
                [item.listing_id for item in items]
            )
        result: list[ListingCard] = []
        for item in items:
            card = cards.get(item.listing_id)
            if card is None:
                continue
            result.append(replace(card, first_price=item.first_price))
        return result


class ListFavorites:
    """Отдать страницу избранного без OFFSET."""

    def __init__(self, uow: UnitOfWork, favorites: FavoriteRepository) -> None:
        self._uow = uow
        self._favorites = favorites

    async def execute(self, query: ListFavoritesQuery) -> Page[FavoriteItem]:
        """Выполнить запрос страницы.

        Args:
            query: Пользователь, лимит и курсор.

        Returns:
            Страница с `next_cursor`, если есть продолжение.
        """
        limit = max(1, min(query.limit, FAVORITES_MAX_LIMIT))
        async with self._uow:
            items, has_more = await self._favorites.page(
                query.user_id,
                limit=limit,
                cursor=query.cursor,
                offset=max(0, query.offset),
            )

        next_cursor: str | None = None
        if has_more and items:
            last = items[-1]
            next_cursor = CursorCodec.encode(
                {
                    "created_at": last.created_at.isoformat(),
                    "listing_id": last.listing_id,
                }
            )
        return Page(items=items, has_more=has_more, next_cursor=next_cursor)
