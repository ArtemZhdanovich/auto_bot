"""SQLAlchemy-реализация порта избранного."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import delete, select, text, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.db.models import Favorite, ListingPriceHistory
from app.domain.favorites.ports import FavoriteCursor, FavoriteItem

# Один атомарный statement: INSERT ... ON CONFLICT DO NOTHING + DELETE,
# если строка уже была. Никакого read-then-write и 500 на гонке.
_TOGGLE_SQL = text(
    """
    WITH ins AS (
        INSERT INTO favorites (user_id, listing_id, created_at)
        VALUES (:user_id, :listing_id, now())
        ON CONFLICT (user_id, listing_id) DO NOTHING
        RETURNING 1
    ), del AS (
        DELETE FROM favorites
        WHERE user_id = :user_id
          AND listing_id = :listing_id
          AND NOT EXISTS (SELECT 1 FROM ins)
        RETURNING 1
    )
    SELECT EXISTS (SELECT 1 FROM ins) AS favorited
    """
)


class SqlAlchemyFavoriteRepository:
    """Репозиторий избранного на SQLAlchemy 2.0 async."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def toggle(self, user_id: int, listing_id: int) -> bool:
        """Атомарно переключить избранное."""
        result = await self._session.execute(
            _TOGGLE_SQL, {"user_id": user_id, "listing_id": listing_id}
        )
        return bool(result.scalar_one())

    async def exists(self, user_id: int, listing_id: int) -> bool:
        """Есть ли запись в избранном."""
        stmt = select(Favorite.user_id).where(
            Favorite.user_id == user_id,
            Favorite.listing_id == listing_id,
        )
        return (await self._session.execute(stmt)).scalar_one_or_none() is not None

    async def remove(self, user_id: int, listing_id: int) -> bool:
        """Идемпотентное удаление: `True`, если строка была."""
        result = await self._session.execute(
            delete(Favorite).where(
                Favorite.user_id == user_id,
                Favorite.listing_id == listing_id,
            )
        )
        return bool(result.rowcount)

    async def page(
        self,
        user_id: int,
        *,
        limit: int,
        cursor: FavoriteCursor | None,
        offset: int = 0,
    ) -> tuple[list[FavoriteItem], bool]:
        """Страница избранного с keyset-курсором."""
        # Первая цена — коррелированный подзапрос: одна строка на объявление,
        # без DISTINCT ON (он deprecated в SQLAlchemy 2.1) и без загрузки
        # всей истории в память. Индекс (listing_id, recorded_at) покрывает.
        first_price = (
            select(ListingPriceHistory.price)
            .where(ListingPriceHistory.listing_id == Favorite.listing_id)
            .order_by(
                ListingPriceHistory.recorded_at.asc(),
                ListingPriceHistory.id.asc(),
            )
            .limit(1)
            .correlate(Favorite)
            .scalar_subquery()
        )

        stmt = (
            select(
                Favorite.listing_id,
                Favorite.created_at,
                first_price.label("price"),
            )
            .where(Favorite.user_id == user_id)
            .order_by(Favorite.created_at.desc(), Favorite.listing_id.desc())
            .limit(limit + 1)
        )
        if cursor is not None:
            stmt = stmt.where(
                tuple_(Favorite.created_at, Favorite.listing_id)
                < tuple_(cursor.created_at, cursor.listing_id)
            )
        elif offset:
            stmt = stmt.offset(offset)

        rows = (await self._session.execute(stmt)).all()
        has_more = len(rows) > limit
        return [self._to_item(row) for row in rows[:limit]], has_more

    @staticmethod
    def _to_item(row) -> FavoriteItem:
        price: Decimal | None = row.price
        return FavoriteItem(
            listing_id=row.listing_id,
            created_at=row.created_at,
            first_price=str(price) if price is not None else None,
        )
