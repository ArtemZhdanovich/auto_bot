"""Поиск по объявлениям (preview_search агента; лента — этап 5)."""

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Listing
from app.schemas.criteria import SearchCriteria


def build_listing_query(criteria: SearchCriteria) -> Select:
    stmt = select(Listing)
    if criteria.make:
        stmt = stmt.where(Listing.make.ilike(criteria.make))
    if criteria.model:
        stmt = stmt.where(Listing.model.ilike(criteria.model))
    if criteria.year_from:
        stmt = stmt.where(Listing.year >= criteria.year_from)
    if criteria.year_to:
        stmt = stmt.where(Listing.year <= criteria.year_to)
    if criteria.price_min:
        stmt = stmt.where(Listing.price >= criteria.price_min)
    if criteria.price_max:
        stmt = stmt.where(Listing.price <= criteria.price_max)
    if criteria.mileage_max:
        stmt = stmt.where(Listing.mileage_km <= criteria.mileage_max)
    # Спеки в listings (этап 5, миграция 0016): кузов/топливо/КПП/привод.
    # Нераспознанные адаптером значения лежат «как есть» — фильтр ilike
    # ловит и канон ('suv'), и сырое значение источника.
    for field, column in (
        ("body_type", Listing.body_type),
        ("fuel", Listing.fuel),
        ("transmission", Listing.transmission),
        ("drive", Listing.drive),
    ):
        value = getattr(criteria, field)
        if value:
            stmt = stmt.where(column.ilike(value))
    if criteria.country:
        # источник знает страну через capabilities; в listings страна не хранится —
        # фильтруем по источникам (encar/kcar = KR), уточняется в этапе 5
        kr_sources = ["encar", "kcar"]
        stmt = stmt.where(Listing.source.in_(kr_sources))
    if criteria.sources:
        stmt = stmt.where(Listing.source.in_(criteria.sources))
    return stmt.order_by(Listing.parsed_at.desc())


async def preview_listings(
    session: AsyncSession, criteria: SearchCriteria, limit: int = 3
) -> list[Listing]:
    stmt = build_listing_query(criteria).limit(limit)
    return list((await session.execute(stmt)).scalars().all())
