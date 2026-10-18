"""Поиск по объявлениям (preview_search агента; лента — этап 5)."""

from decimal import Decimal

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
        ("color", Listing.color),
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


# Порядок ослабления критериев («пусто по точным — покажи близкие»):
# сначала жертвуем косметикой (цвет), потом пробегом, спеками, годами, ценой.
_RELAX_STEPS: list[tuple[str, str]] = [
    ("color", "цвет"),
    ("mileage_max", "пробег"),
    ("body_type", "кузов"),
    ("fuel", "топливо"),
    ("transmission", "КПП"),
    ("drive", "привод"),
    ("year", "год"),
    ("price_max", "бюджет +20%"),
]


def _relaxed(criteria: SearchCriteria, step_name: str) -> SearchCriteria:
    """Копия критериев с ослаблением одного параметра."""
    upd: dict[str, object] = {}
    if step_name in ("color", "mileage_max", "body_type", "fuel", "transmission", "drive"):
        upd[step_name] = None
    elif step_name == "year":
        if criteria.year_from:
            upd["year_from"] = max(criteria.year_from - 3, 1980)
        if criteria.year_to:
            upd["year_to"] = criteria.year_to + 3
        if not upd:
            return criteria  # годы не заданы — шаг пропускается
    elif step_name == "price_max":
        if not criteria.price_max:
            return criteria
        upd["price_max"] = Decimal(str(criteria.price_max)) * Decimal("1.2")
    return criteria.model_copy(update=upd)


async def search_with_relaxation(
    session: AsyncSession, criteria: SearchCriteria, limit: int = 3
) -> tuple[list[Listing], list[str]]:
    """Поиск «точный → с ослаблением».

    Возвращает (объявления, список ослабленных параметров по-русски).
    Критерии пользователя не меняются — ослабление только для запроса.
    """
    items = await preview_listings(session, criteria, limit=limit)
    if items:
        return items, []

    dropped: list[str] = []
    current = criteria
    for step_name, label in _RELAX_STEPS:
        candidate = _relaxed(current, step_name)
        if candidate == current:
            continue  # параметра не было — ослаблять нечего
        current = candidate
        dropped.append(label)
        items = await preview_listings(session, current, limit=limit)
        if items:
            break
    return items, dropped
