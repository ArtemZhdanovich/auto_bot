"""SQLAlchemy-реализация порта объявлений."""

from __future__ import annotations

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.catalog.dto import ListingCard
from app.infrastructure.db.models import Listing


class SqlAlchemyListingRepository:
    """Минимальное чтение объявлений для сценариев."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def exists_active(self, listing_id: int) -> bool:
        """Есть ли активное объявление с таким id."""
        stmt = select(
            exists().where(
                Listing.id == listing_id,
                Listing.is_active.is_(True),
            )
        )
        return bool((await self._session.execute(stmt)).scalar())

    async def cards_by_ids(self, listing_ids: list[int]) -> dict[int, ListingCard]:
        """Карточки по id одним запросом: {id: ListingCard}."""
        if not listing_ids:
            return {}
        rows = (
            await self._session.execute(
                select(Listing).where(Listing.id.in_(listing_ids))
            )
        ).scalars().all()
        return {row.id: _to_card(row) for row in rows}


def _to_card(row: Listing) -> ListingCard:
    """ORM → DTO (единственное место, где они встречаются)."""
    return ListingCard(
        id=row.id,
        source=row.source,
        source_id=row.source_id,
        url=row.url,
        make=row.make,
        model=row.model,
        year=row.year,
        price=row.price,
        currency=row.currency,
        mileage_km=row.mileage_km,
        vin=row.vin,
        body_type=row.body_type,
        fuel=row.fuel,
        transmission=row.transmission,
        drive=row.drive,
        engine_volume=row.engine_volume,
        color=row.color,
        images=tuple(row.images or ()),
        parsed_at=row.parsed_at,
        is_active=row.is_active,
        first_seen_at=row.first_seen_at,
        last_seen_at=row.last_seen_at,
        updated_at=row.updated_at,
        price_changed_at=row.price_changed_at,
        title_raw=row.title_raw,
        title_ru=row.title_ru,
        source_language=row.source_language,
    )
