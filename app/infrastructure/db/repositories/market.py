"""SQL-реализация рыночной аналитики партнёрского контура.

Срез рынка — активные объявления с ценой (+опц. марка/модель, регистр
не важен). Медиану считаем в Python по выборке цен (каталог может быть
большим, а партнёру точность перцентиля не критична); count/min/max —
точные, одним агрегатом. «Выгодные» лоты (deal) — дешевле 93% медианы.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.partners.market import MarketReport, MarketSummary
from app.infrastructure.db.models import Listing

# Верхняя граница выборки цен для медианы: точный перцентиль по всему
# каталогу не окупается, выборка в 1000 строк достаточна и предсказуема.
_PRICE_SAMPLE_ROWS = 1000

# Лот считается выгодным, если он дешевле 93% медианы среза.
_DEAL_FACTOR = Decimal("0.93")


def _normalize(value: str | None) -> str | None:
    """Пустая строка → `None` (фильтр не задан)."""
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _market_filters(
    make: str | None, model: str | None
) -> list[ColumnElement[bool]]:
    """Условия среза рынка: активные лоты с ценой (+марка/модель)."""
    filters: list[ColumnElement[bool]] = [
        Listing.is_active.is_(True),
        Listing.price > 0,
    ]
    if make:
        filters.append(func.lower(Listing.make) == make.lower())
    if model:
        filters.append(func.lower(Listing.model) == model.lower())
    return filters


def _median(prices: list[Decimal]) -> float | None:
    """Медиана выборки цен (сортировка в Python)."""
    if not prices:
        return None
    ordered = sorted(prices)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[mid])
    return float((ordered[mid - 1] + ordered[mid]) / 2)


class SqlAlchemyPartnerMarketRepository:
    """Порт `PartnerMarketRepository` на `AsyncSession`."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def report(
        self,
        *,
        make: str | None = None,
        model: str | None = None,
        deals_limit: int = 10,
    ) -> MarketReport:
        """Сводка среза + выгодные лоты (цена < 93% медианы).

        `make` не задан — весь каталог: общая медиана, без группировки
        по моделям. Deals отсортированы по выгоде (дешевле — выше).
        """
        make = _normalize(make)
        model = _normalize(model)
        filters = _market_filters(make, model)

        total, min_price, max_price = (
            await self._session.execute(
                select(
                    func.count(),
                    func.min(Listing.price),
                    func.max(Listing.price),
                ).where(*filters)
            )
        ).one()
        if not total:
            return MarketReport(summary=MarketSummary(make=make, model=model))

        prices = list(
            (
                await self._session.execute(
                    select(Listing.price)
                    .where(*filters)
                    .order_by(Listing.id)
                    .limit(_PRICE_SAMPLE_ROWS)
                )
            )
            .scalars()
            .all()
        )
        median = _median(prices)

        deals: list[dict[str, Any]] = []
        if median is not None:
            # price ASC == discount DESC: медиана у среза одна, чем дешевле
            # лот, тем больше его скидка к медиане.
            threshold = Decimal(str(median)) * _DEAL_FACTOR
            rows = (
                await self._session.execute(
                    select(
                        Listing.id,
                        Listing.make,
                        Listing.model,
                        Listing.year,
                        Listing.price,
                        Listing.currency,
                        Listing.mileage_km,
                        Listing.url,
                    )
                    .where(*filters)
                    .where(Listing.price < threshold)
                    .order_by(Listing.price.asc())
                    .limit(deals_limit)
                )
            ).all()
            deals = [
                {
                    "id": row.id,
                    "make": row.make,
                    "model": row.model,
                    "year": row.year,
                    "price": float(row.price),
                    "currency": row.currency,
                    "mileage_km": row.mileage_km,
                    "url": row.url,
                    "discount_pct": round(
                        (1 - float(row.price) / median) * 100, 1
                    ),
                }
                for row in rows
            ]

        return MarketReport(
            summary=MarketSummary(
                make=make,
                model=model,
                count=int(total),
                min_price=float(min_price) if min_price is not None else None,
                max_price=float(max_price) if max_price is not None else None,
                median_price=median,
            ),
            deals=deals,
        )
