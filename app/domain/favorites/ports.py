"""Порт избранного: операции, которые нужны сценариям."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True, slots=True)
class FavoriteCursor:
    """Позиция в keyset-пагинации избранного."""

    created_at: datetime
    listing_id: int


@dataclass(frozen=True, slots=True)
class FavoriteItem:
    """Элемент списка избранного."""

    listing_id: int
    created_at: datetime
    first_price: str | None = None


class FavoriteRepository(Protocol):
    """Хранилище избранного.

    Реализация обязана обеспечивать атомарность `toggle` (без read-then-write)
    и стабильный порядок `created_at DESC, listing_id DESC`.
    """

    async def toggle(self, user_id: int, listing_id: int) -> bool:
        """Переключить состояние и вернуть итоговое значение `favorited`."""

    async def exists(self, user_id: int, listing_id: int) -> bool:
        """Состоит ли объявление в избранном у пользователя."""

    async def remove(self, user_id: int, listing_id: int) -> bool:
        """Идемпотентно удалить; `True`, если строка была."""

    async def page(
        self,
        user_id: int,
        *,
        limit: int,
        cursor: FavoriteCursor | None,
        offset: int = 0,
    ) -> tuple[list[FavoriteItem], bool]:
        """Вернуть страницу и признак «есть ещё».

        `cursor` (keyset) и `offset` взаимоисключающие: cursor приоритетнее.
        """
