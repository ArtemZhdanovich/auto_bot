"""Лента объявлений для Mini App (этап 5, ТЗ п. 9.2.4–9.2.5)."""

import re

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import case, func, select

from app.core.deps import SessionDep
from app.db.models import Listing, ListingPriceHistory
from app.schemas.listing import ListingListOut, ListingOut, PricePointOut
from app.services.analytics import EVENT_SEARCH_COMPLETED, track

router = APIRouter(prefix="/api/listings", tags=["listings"])


@router.get("", response_model=ListingListOut)
async def list_listings(
    session: SessionDep,
    make: str | None = None,
    model: str | None = None,
    year_from: int | None = Query(default=None, ge=1900),
    year_to: int | None = Query(default=None, ge=1900),
    price_min: float | None = Query(default=None, ge=0),
    price_max: float | None = Query(default=None, ge=0),
    currency: str | None = None,
    mileage_max: int | None = Query(default=None, ge=0),
    # Спеки (миграция 0016): кузов/топливо/КПП/привод — фильтры ленты.
    body_type: str | None = None,
    fuel: str | None = None,
    transmission: str | None = None,
    drive: str | None = None,
    source: str | None = None,
    sort: str = Query(default="new", pattern="^(new|price_asc|price_desc|mileage_asc|year_desc)$"),
    limit: int = Query(default=20, ge=1, le=50),
    offset: int = Query(default=0, ge=0),
) -> ListingListOut:
    """Лента с фильтрами и сортировкой (пагинация limit/offset).

    MVP: только Корея — если source не указан явно, отдаём encar+kcar.
    """
    conditions = []
    if source:
        conditions.append(func.lower(Listing.source) == source.lower())
    else:
        conditions.append(Listing.source.in_(("encar", "kcar")))
    if make:
        conditions.append(func.lower(Listing.make) == make.lower())
    if model:
        conditions.append(func.lower(Listing.model) == model.lower())
    if year_from is not None:
        conditions.append(Listing.year >= year_from)
    if year_to is not None:
        conditions.append(Listing.year <= year_to)
    if price_min is not None:
        conditions.append(Listing.price >= price_min)
    if price_max is not None:
        conditions.append(Listing.price <= price_max)
    if currency:
        conditions.append(func.lower(Listing.currency) == currency.lower())
    if mileage_max is not None:
        conditions.append(Listing.mileage_km <= mileage_max)
    for param, column in (
        (body_type, Listing.body_type),
        (fuel, Listing.fuel),
        (transmission, Listing.transmission),
        (drive, Listing.drive),
    ):
        if param:
            conditions.append(column.ilike(param))
    query = select(Listing)
    count_query = select(func.count()).select_from(Listing)
    if conditions:
        query = query.where(*conditions)
        count_query = count_query.where(*conditions)

    total = (await session.execute(count_query)).scalar_one()
    # Сортировка (e-commerce стандарт PLP): объявления без цены всегда внизу.
    # В БД есть USD- и KRW-объявления — сортируем по цене, нормализованной в $.
    usd_price = case(
        (func.upper(Listing.currency) == "KRW", Listing.price / 1350.0),
        else_=Listing.price,
    )
    order = {
        "new": (Listing.parsed_at.desc(), Listing.id.desc()),
        "price_asc": (usd_price.asc().nulls_last(), Listing.id.desc()),
        "price_desc": (usd_price.desc().nulls_last(), Listing.id.desc()),
        "mileage_asc": (Listing.mileage_km.asc().nulls_last(), Listing.id.desc()),
        "year_desc": (Listing.year.desc().nulls_last(), Listing.id.desc()),
    }[sort]
    result = await session.execute(query.order_by(*order).limit(limit).offset(offset))
    items = [ListingOut.model_validate(x) for x in result.scalars().all()]
    if conditions:  # это именно поиск с фильтрами, а не просмотр ленты
        await track(
            session,
            None,
            EVENT_SEARCH_COMPLETED,
            {"results": total, "filters": len(conditions)},
        )
    return ListingListOut(total=total, items=items)


@router.get("/by-url", response_model=ListingOut)
async def get_listing_by_url(
    session: SessionDep,
    url: str = Query(min_length=8, max_length=512),
) -> ListingOut:
    """Объявление по ссылке — бот: «скинь ссылку, покажу карточку».

    Сравнение по нормализованному хвосту URL: без схемы, www/m.,
    завершающего слеша (пользователь копирует ссылку в любом виде).
    """
    needle = _norm_url(url)
    result = await session.execute(
        select(Listing).where(Listing.url.ilike(f"%{needle}%"))
    )
    listing = result.scalar_one_or_none()
    if listing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Listing not found"
        )
    return ListingOut.model_validate(listing)


def _norm_url(url: str) -> str:
    """Нормализация ссылки для поиска: без схемы, www/m., финального слеша."""
    u = url.strip().lower()
    u = re.sub(r"^https?://", "", u)
    u = re.sub(r"^(www|m)\.", "", u)
    return u.rstrip("/")


@router.get("/{listing_id}", response_model=ListingOut)
async def get_listing(
    listing_id: int, session: SessionDep
) -> ListingOut:
    listing = await session.get(Listing, listing_id)
    if listing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Listing not found"
        )
    out = ListingOut.model_validate(listing)
    # История цен — только в карточке (в ленте был бы N+1); хронологический порядок
    result = await session.execute(
        select(
            ListingPriceHistory.price,
            ListingPriceHistory.currency,
            ListingPriceHistory.recorded_at,
        )
        .where(ListingPriceHistory.listing_id == listing_id)
        .order_by(ListingPriceHistory.recorded_at, ListingPriceHistory.id)
    )
    out.price_history = [
        PricePointOut.model_validate(row, from_attributes=True)
        for row in result.all()
    ]
    return out
