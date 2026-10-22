"""SQLAlchemy-реализация порта разбора цены (кэш, рынок, квота)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.pricing.ports import (
    ListingQuery,
    MarketContext,
    PriceAnalysisRecord,
)
from app.infrastructure.db.models import Listing, ListingPriceHistory, PriceAnalysis

COMPS_LIMIT = 30  # столько сопоставимых хватает медиане
COMPS_YEAR_SPAN = 2  # ±2 года к объявлению


class SqlAlchemyPriceAnalysisRepository:
    """Разборы цены на SQLAlchemy 2.0 async."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def count_recent(
        self, user_id: int, listing_id: int, *, since: datetime
    ) -> int:
        """Сколько разборов этой машины юзер запросил с `since`."""
        result = await self._session.execute(
            select(func.count())
            .select_from(PriceAnalysis)
            .where(
                PriceAnalysis.user_id == user_id,
                PriceAnalysis.listing_id == listing_id,
                PriceAnalysis.created_at >= since,
            )
        )
        return int(result.scalar_one())

    async def context(self, listing: ListingQuery) -> MarketContext:
        """Сопоставимые цены (та же модель, ±2 года) и история цены."""
        prices: list[float] = []
        if listing.make and listing.model and listing.year:
            result = await self._session.execute(
                select(Listing.price)
                .where(
                    Listing.id != listing.id,
                    Listing.price.isnot(None),
                    Listing.price > 0,
                    func.lower(Listing.make) == listing.make.lower(),
                    func.lower(Listing.model) == listing.model.lower(),
                    Listing.year.between(
                        listing.year - COMPS_YEAR_SPAN, listing.year + COMPS_YEAR_SPAN
                    ),
                )
                .limit(COMPS_LIMIT)
            )
            prices = [float(price) for (price,) in result.all()]

        hist_result = await self._session.execute(
            select(ListingPriceHistory.price, ListingPriceHistory.recorded_at)
            .where(ListingPriceHistory.listing_id == listing.id)
            .order_by(ListingPriceHistory.recorded_at, ListingPriceHistory.id)
        )
        history = tuple(
            {"price": float(price), "recorded_at": str(recorded_at)}
            for price, recorded_at in hist_result.all()
        )
        return MarketContext(comp_prices=tuple(prices), history=history)

    async def save(self, record: PriceAnalysisRecord) -> None:
        """Поставить строку разбора в текущую транзакцию."""
        self._session.add(
            PriceAnalysis(
                listing_id=record.listing_id,
                user_id=record.user_id,
                verdict=record.verdict,
                fair_min=record.fair_min,
                fair_max=record.fair_max,
                summary=record.summary,
                payload=record.payload,
            )
        )

    async def latest_payload(self, listing_id: int) -> dict[str, Any] | None:
        """Payload последнего разбора листинга (общий кэш)."""
        result = await self._session.execute(
            select(PriceAnalysis)
            .where(PriceAnalysis.listing_id == listing_id)
            .order_by(PriceAnalysis.created_at.desc(), PriceAnalysis.id.desc())
            .limit(1)
        )
        row = result.scalars().first()
        return dict(row.payload or {}) if row else None
