"""SQLAlchemy-реализация чтения объявлений: лента, facets, URL, PDP, осмотр.

Здесь живёт весь SQL read-эндпоинтов `/api/listings`: нормализация
make/model/спеков (`app.infrastructure.sources.common.normalize`), цена в
USD, keyset-условие, `count(*)` только в offset-режиме и запись события
поиска в analytics.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import case, func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.catalog.dto import (
    FeedCursor,
    InspectionUpdate,
    InspectionView,
    ListingCard,
    ListingFeedQuery,
    PricePoint,
)
from app.infrastructure.db.models import (
    InspectionReport,
    Listing,
    ListingPriceHistory,
)
from app.infrastructure.services.analytics import EVENT_SEARCH_COMPLETED, track
from app.infrastructure.services.fetch import enrich_listing_specs
from app.infrastructure.sources.common.normalize import (
    normalize_body,
    normalize_color,
    normalize_drive,
    normalize_fuel,
    normalize_make,
    normalize_transmission,
)

# Курс для нормализации сортировки/фильтра в USD. TODO: вынести в Settings.usd_krw.
_USD_KRW_FALLBACK = 1350.0
_FACETS_LIMIT = 500


def _usd_price_expr():
    """SQL-выражение `price` в USD (KRW нормализуется по курсу)."""
    return case(
        (func.upper(Listing.currency) == "KRW", Listing.price / _USD_KRW_FALLBACK),
        else_=Listing.price,
    )


def _escape_like(value: str) -> str:
    """Экранирует `%`, `_` и `\\` для `ILIKE ... ESCAPE '\\\\'`."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _to_card(row: Listing) -> ListingCard:
    """ORM → DTO (единственное место, где они встречаются)."""
    is_active = getattr(row, "is_active", True)
    return ListingCard(
        id=row.id,
        source=row.source,
        source_id=row.source_id,
        url=row.url,
        parsed_at=row.parsed_at,
        make=getattr(row, "make", None),
        model=getattr(row, "model", None),
        year=getattr(row, "year", None),
        price=getattr(row, "price", None),
        currency=getattr(row, "currency", None),
        mileage_km=getattr(row, "mileage_km", None),
        vin=getattr(row, "vin", None),
        body_type=getattr(row, "body_type", None),
        fuel=getattr(row, "fuel", None),
        transmission=getattr(row, "transmission", None),
        drive=getattr(row, "drive", None),
        engine_volume=getattr(row, "engine_volume", None),
        color=getattr(row, "color", None),
        images=tuple(getattr(row, "images", None) or ()),
        is_active=True if is_active is None else bool(is_active),
        first_seen_at=getattr(row, "first_seen_at", None),
        last_seen_at=getattr(row, "last_seen_at", None),
        updated_at=getattr(row, "updated_at", None),
        price_changed_at=getattr(row, "price_changed_at", None),
        title_raw=getattr(row, "title_raw", None),
        title_ru=getattr(row, "title_ru", None),
        source_language=getattr(row, "source_language", None),
    )


def _to_inspection(row: InspectionReport) -> InspectionView:
    """ORM → DTO отчёта осмотра."""
    return InspectionView(
        source=row.source or "manual",
        inspector=getattr(row, "inspector", None),
        inspected_at=getattr(row, "inspected_at", None),
        grade=getattr(row, "grade", None),
        summary=getattr(row, "summary", None),
        marks=[dict(mark) for mark in (getattr(row, "marks", None) or [])],
        items=[dict(item) for item in (getattr(row, "items", None) or [])],
    )


class SqlAlchemyListingReadRepository:
    """Репозиторий чтения объявлений на SQLAlchemy 2.0 async."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # Лента
    # ------------------------------------------------------------------

    async def page(
        self, query: ListingFeedQuery, cursor: FeedCursor | None
    ) -> tuple[list[ListingCard], int | None, bool]:
        """Страница ленты, `total` (None в keyset) и `has_more`."""
        conditions = self._conditions(query)
        usd_price = _usd_price_expr()
        order = {
            "new": (Listing.parsed_at.desc(), Listing.id.desc()),
            "price_asc": (usd_price.asc().nulls_last(), Listing.id.desc()),
            "price_desc": (usd_price.desc().nulls_last(), Listing.id.desc()),
            "mileage_asc": (
                Listing.mileage_km.asc().nulls_last(),
                Listing.id.desc(),
            ),
            "year_desc": (Listing.year.desc().nulls_last(), Listing.id.desc()),
        }[query.sort]

        limit = max(1, query.limit)
        stmt = select(Listing).where(*conditions)
        if cursor is not None:
            # Keyset: строго после позиции курсора, без offset и count(*).
            stmt = stmt.where(
                tuple_(Listing.parsed_at, Listing.id)
                < tuple_(cursor.parsed_at, cursor.listing_id)
            )
        elif query.offset:
            stmt = stmt.offset(query.offset)

        # limit + 1 — чтобы отличить «страница заполнена» от «есть продолжение».
        rows = (
            await self._session.execute(
                stmt.order_by(*order).limit(limit + 1)
            )
        ).scalars().all()
        has_more = len(rows) > limit
        items = [_to_card(row) for row in rows[:limit]]

        total: int | None = None
        if cursor is None:
            total = (
                await self._session.execute(
                    select(func.count()).select_from(Listing).where(*conditions)
                )
            ).scalar_one()
        return items, total, has_more

    def _conditions(self, query: ListingFeedQuery) -> list:
        """SQL-условия ленты; нормализация — на границе адаптера."""
        # is_active — обязательный фильтр, не считается «пользовательским».
        conditions: list = [Listing.is_active.is_(True)]

        if query.source:
            conditions.append(func.lower(Listing.source) == query.source.lower())
        else:
            # MVP: только Корея — без `source` отдаём encar+kcar.
            conditions.append(func.lower(Listing.source).in_(("encar", "kcar")))

        if query.make:
            m = (normalize_make(query.make) or query.make).lower()
            conditions.append(func.lower(Listing.make) == m)
        if query.model:
            conditions.append(func.lower(Listing.model) == query.model.lower())

        if query.year_from is not None:
            conditions.append(Listing.year >= query.year_from)
        if query.year_to is not None:
            conditions.append(Listing.year <= query.year_to)

        # Цена: нормализуем в USD (как и сортировка).
        usd_price = _usd_price_expr()
        if query.price_min is not None:
            conditions.append(usd_price >= query.price_min)
        if query.price_max is not None:
            conditions.append(usd_price <= query.price_max)

        if query.currency:
            conditions.append(func.lower(Listing.currency) == query.currency.lower())

        if query.mileage_min is not None:
            conditions.append(Listing.mileage_km >= query.mileage_min)
        if query.mileage_max is not None:
            conditions.append(Listing.mileage_km <= query.mileage_max)

        # Спеки: нормализуем к канону (sedan/suv/petrol/...) и сравниваем
        # case-insensitive. Сырое значение — fallback, если нормализатор
        # не знает (например, «купе-стрела»).
        for param, column, normalizer in (
            (query.body_type, Listing.body_type, normalize_body),
            (query.fuel, Listing.fuel, normalize_fuel),
            (query.transmission, Listing.transmission, normalize_transmission),
            (query.drive, Listing.drive, normalize_drive),
            (query.color, Listing.color, normalize_color),
        ):
            if param:
                normalized = normalizer(param) or param
                conditions.append(func.lower(column) == normalized.lower())

        if query.engine_volume_min is not None:
            conditions.append(Listing.engine_volume >= query.engine_volume_min)
        if query.engine_volume_max is not None:
            conditions.append(Listing.engine_volume <= query.engine_volume_max)

        return conditions

    async def record_search(
        self, user_id: int | None, *, results: int, filters: int
    ) -> None:
        """Записать `search_completed` (сбой не глотается — решает сценарий)."""
        await track(
            self._session,
            user_id,
            EVENT_SEARCH_COMPLETED,
            {"results": results, "filters": filters},
        )

    # ------------------------------------------------------------------
    # Facets
    # ------------------------------------------------------------------

    async def facets(self, make: str | None) -> dict[str, list[str]]:
        """Марки (без `make`) или модели выбранной марки."""
        if make:
            m_norm = (normalize_make(make) or make).lower()
            rows = (
                await self._session.execute(
                    select(Listing.model)
                    .where(
                        Listing.is_active.is_(True),
                        func.lower(Listing.make) == m_norm,
                        Listing.model.is_not(None),
                        Listing.model != "",
                    )
                    .distinct()
                    .order_by(Listing.model)
                    .limit(_FACETS_LIMIT)
                )
            ).all()
            return {"models": [row[0] for row in rows]}

        rows = (
            await self._session.execute(
                select(Listing.make)
                .where(
                    Listing.is_active.is_(True),
                    Listing.make.is_not(None),
                    Listing.make != "",
                )
                .distinct()
                .order_by(Listing.make)
                .limit(_FACETS_LIMIT)
            )
        ).all()
        return {"makes": [row[0] for row in rows]}

    # ------------------------------------------------------------------
    # By URL
    # ------------------------------------------------------------------

    async def find_by_url(self, needle: str) -> list[ListingCard]:
        """До двух активных объявлений по нормализованному хвосту URL."""
        escaped = _escape_like(needle)
        rows = (
            await self._session.execute(
                select(Listing)
                .where(
                    Listing.url.ilike(f"%{escaped}%", escape="\\"),
                    Listing.is_active.is_(True),
                )
                .limit(2)
            )
        ).scalars().all()
        return [_to_card(row) for row in rows]

    # ------------------------------------------------------------------
    # PDP
    # ------------------------------------------------------------------

    async def get_active(self, listing_id: int) -> ListingCard | None:
        """Активное объявление или None."""
        row = await self._session.get(Listing, listing_id)
        if row is None or not getattr(row, "is_active", True):
            return None
        return _to_card(row)

    async def enrich(self, listing_id: int) -> ListingCard | None:
        """Догрузить спеки из detail источника и вернуть карточку."""
        row = await self._session.get(Listing, listing_id)
        if row is None:
            return None
        await enrich_listing_specs(self._session, row)
        return _to_card(row)

    async def price_history(self, listing_id: int) -> list[PricePoint]:
        """История цен объявления (хронологически)."""
        rows = (
            await self._session.execute(
                select(
                    ListingPriceHistory.price,
                    ListingPriceHistory.currency,
                    ListingPriceHistory.recorded_at,
                )
                .where(ListingPriceHistory.listing_id == listing_id)
                .order_by(
                    ListingPriceHistory.recorded_at, ListingPriceHistory.id
                )
            )
        ).all()
        return [
            PricePoint(
                price=Decimal(row.price),
                currency=row.currency,
                recorded_at=row.recorded_at,
            )
            for row in rows
        ]

    # ------------------------------------------------------------------
    # Осмотр
    # ------------------------------------------------------------------

    async def exists(self, listing_id: int) -> bool:
        """Есть ли объявление с таким id (в любом статусе)."""
        return await self._session.get(Listing, listing_id) is not None

    async def get_report(self, listing_id: int) -> InspectionView | None:
        """Отчёт осмотра или None."""
        row = (
            await self._session.execute(
                select(InspectionReport).where(
                    InspectionReport.listing_id == listing_id
                )
            )
        ).scalar_one_or_none()
        return _to_inspection(row) if row is not None else None

    async def upsert_report(
        self, listing_id: int, update: InspectionUpdate
    ) -> InspectionView:
        """Создать/заменить отчёт осмотра (commit — в сценарии)."""
        row = (
            await self._session.execute(
                select(InspectionReport).where(
                    InspectionReport.listing_id == listing_id
                )
            )
        ).scalar_one_or_none()
        if row is None:
            row = InspectionReport(listing_id=listing_id)
            self._session.add(row)

        row.source = update.source
        row.inspector = update.inspector
        row.inspected_at = update.inspected_at
        row.grade = update.grade
        row.summary = update.summary
        row.marks = [dict(mark) for mark in update.marks]
        row.items = [dict(item) for item in update.items]
        row.updated_at = datetime.now(UTC)
        await self._session.flush()
        return _to_inspection(row)
