"""Лента объявлений для Mini App (этап 5, ТЗ п. 9.2.4–9.2.5)."""

import logging
import re

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import case, func, select

from app.core.deps import CurrentUserDep, SessionDep
from app.db.models import InspectionReport, Listing, ListingPriceHistory
from app.schemas.listing import (
    InspectionReportIn,
    InspectionReportOut,
    ListingListOut,
    ListingOut,
    PricePointOut,
)
from app.services.analytics import EVENT_SEARCH_COMPLETED, track
from app.services.fetch import enrich_listing_specs

logger = logging.getLogger(__name__)

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
    mileage_min: int | None = Query(default=None, ge=0),
    mileage_max: int | None = Query(default=None, ge=0),
    # Спеки (миграция 0016): кузов/топливо/КПП/привод — фильтры ленты.
    body_type: str | None = None,
    fuel: str | None = None,
    transmission: str | None = None,
    drive: str | None = None,
    # Детализация (миграция 0017): объём двигателя от/до (литры), цвет.
    engine_volume_min: float | None = Query(default=None, ge=0, le=20),
    engine_volume_max: float | None = Query(default=None, ge=0, le=20),
    color: str | None = None,
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
    if mileage_min is not None:
        conditions.append(Listing.mileage_km >= mileage_min)
    if mileage_max is not None:
        conditions.append(Listing.mileage_km <= mileage_max)
    for param, column in (
        (body_type, Listing.body_type),
        (fuel, Listing.fuel),
        (transmission, Listing.transmission),
        (drive, Listing.drive),
        (color, Listing.color),
    ):
        if param:
            conditions.append(column.ilike(param))
    if engine_volume_min is not None:
        conditions.append(Listing.engine_volume >= engine_volume_min)
    if engine_volume_max is not None:
        conditions.append(Listing.engine_volume <= engine_volume_max)
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


@router.get("/facets")
async def listing_facets(
    session: SessionDep,
    make: str | None = None,
) -> dict[str, list[str]]:
    """Справочник для зависимых селектов фильтров (как на avtokorea.by).

    Без make — список всех марок; с make — только модели этой марки.
    Значения — как они есть в БД (encar/kcar отдают англ. названия марок
    и разные варианты моделей), сортировка по алфавиту.
    """
    if make:
        rows = await session.execute(
            select(Listing.model)
            .where(
                func.lower(Listing.make) == make.lower(),
                Listing.model.is_not(None),
                Listing.model != "",
            )
            .distinct()
            .order_by(Listing.model)
        )
        return {"models": [r[0] for r in rows.all()]}
    rows = await session.execute(
        select(Listing.make)
        .where(Listing.make.is_not(None), Listing.make != "")
        .distinct()
        .order_by(Listing.make)
    )
    return {"makes": [r[0] for r in rows.all()]}


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
    # Обогащение из detail encar (миграция 0017): объём/цвет/спеки догружаем
    # лениво при открытии карточки; сбой encar не ломает PDP.
    try:
        await enrich_listing_specs(session, listing)
    except Exception:  # noqa: BLE001
        logger.warning("enrich_failed", exc_info=True)
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


# --- Осмотр (миграция 0018): карта повреждений + чек-лист --------------------


@router.get("/{listing_id}/inspection", response_model=InspectionReportOut)
async def get_inspection(listing_id: int, session: SessionDep) -> InspectionReportOut:
    """Отчёт осмотра для PDP; 404, если осмотра ещё нет."""
    result = await session.execute(
        select(InspectionReport).where(InspectionReport.listing_id == listing_id)
    )
    report = result.scalar_one_or_none()
    if report is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Inspection not found"
        )
    return InspectionReportOut.model_validate(report)


@router.put("/{listing_id}/inspection", response_model=InspectionReportOut)
async def upsert_inspection(
    listing_id: int,
    payload: InspectionReportIn,
    session: SessionDep,
    user: CurrentUserDep,
) -> InspectionReportOut:
    """Создать/заменить отчёт осмотра. Только admin (ручной осмотр)."""
    if getattr(user, "role", None) != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Admin only"
        )
    listing = await session.get(Listing, listing_id)
    if listing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Listing not found"
        )
    result = await session.execute(
        select(InspectionReport).where(InspectionReport.listing_id == listing_id)
    )
    report = result.scalar_one_or_none()
    if report is None:
        report = InspectionReport(listing_id=listing_id)
        session.add(report)
    report.source = payload.source
    report.inspector = payload.inspector
    report.inspected_at = payload.inspected_at
    report.grade = payload.grade
    report.summary = payload.summary
    report.marks = [m.model_dump() for m in payload.marks]
    report.items = [i.model_dump() for i in payload.items]
    report.updated_at = func.now()
    await session.commit()
    await session.refresh(report)
    return InspectionReportOut.model_validate(report)
