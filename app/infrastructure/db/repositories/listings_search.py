"""SQLAlchemy-реализация поиска по объявлениям (preview агента; лента — этап 5).

Условия приходят из домена (`app.domain.search.query.listing_filters`) как
данные; здесь они превращаются в выражения SQL — единственное место в
проекте, где критерии встречаются с колонками.
"""

from __future__ import annotations

from sqlalchemy import ColumnElement, Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.catalog.dto import ListingPreview
from app.domain.search.criteria import SearchCriteria
from app.domain.search.query import (
    OP_GTE,
    OP_ILIKE,
    OP_IN,
    OP_LTE,
    FilterTerm,
    listing_filters,
)
from app.infrastructure.db.models import Listing


def _condition(term: FilterTerm) -> ColumnElement[bool]:
    """Условие SQL по описанию из домена."""
    column = getattr(Listing, term.column)
    if term.op == OP_ILIKE:
        return column.ilike(term.value)
    if term.op == OP_GTE:
        return column >= term.value
    if term.op == OP_LTE:
        return column <= term.value
    if term.op == OP_IN:
        return column.in_(term.value)
    raise ValueError(f"unknown filter op: {term.op}")


def build_listing_query(criteria: SearchCriteria) -> Select[tuple[Listing]]:
    """Запрос объявлений по критериям, свежие первыми.

    Отдельная функция, потому что рассылка (`notify`) наращивает её своими
    условиями (`parsed_at >= since`, `limit`).
    """
    stmt = select(Listing)
    for term in listing_filters(criteria):
        stmt = stmt.where(_condition(term))
    return stmt.order_by(Listing.parsed_at.desc())


class SqlAlchemyListingSearchRepository:
    """Порт поиска: превью объявлений по критериям."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def preview(
        self, criteria: SearchCriteria, limit: int = 3
    ) -> list[ListingPreview]:
        """До `limit` объявлений, свежие первыми."""
        stmt = build_listing_query(criteria).limit(limit)
        rows = (await self._session.execute(stmt)).scalars().all()
        return [_to_preview(row) for row in rows]


def _to_preview(row: Listing) -> ListingPreview:
    """ORM → DTO (единственное место, где они встречаются)."""
    return ListingPreview(
        id=row.id,
        source=row.source,
        url=row.url,
        make=row.make,
        model=row.model,
        year=row.year,
        price=row.price,
        currency=row.currency,
        mileage_km=row.mileage_km,
    )
