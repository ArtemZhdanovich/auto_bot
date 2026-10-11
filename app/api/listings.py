"""Лента объявлений для Mini App (этап 5, ТЗ п. 9.2.4–9.2.5)."""

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select

from app.core.deps import SessionDep
from app.db.models import Listing
from app.schemas.listing import ListingListOut, ListingOut

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
    source: str | None = None,
    limit: int = Query(default=20, ge=1, le=50),
    offset: int = Query(default=0, ge=0),
) -> ListingListOut:
    """Лента с фильтрами, новые сверху (пагинация limit/offset)."""
    conditions = []
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
    if source:
        conditions.append(func.lower(Listing.source) == source.lower())

    query = select(Listing)
    count_query = select(func.count()).select_from(Listing)
    if conditions:
        query = query.where(*conditions)
        count_query = count_query.where(*conditions)

    total = (await session.execute(count_query)).scalar_one()
    result = await session.execute(
        query.order_by(Listing.parsed_at.desc(), Listing.id.desc())
        .limit(limit)
        .offset(offset)
    )
    items = [ListingOut.model_validate(x) for x in result.scalars().all()]
    return ListingListOut(total=total, items=items)


@router.get("/{listing_id}", response_model=ListingOut)
async def get_listing(
    listing_id: int, session: SessionDep
) -> ListingOut:
    listing = await session.get(Listing, listing_id)
    if listing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Listing not found"
        )
    return ListingOut.model_validate(listing)
