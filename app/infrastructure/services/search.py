"""Фасад поиска: тонкая привязка сценария к сессии.

Правила критериев и ослабления — в `app.domain.search.query`, SQL — в
`app.infrastructure.db.repositories.listings_search`. Здесь только
совместимые с прежним API функции; `build_listing_query` ре-экспортируется
для рассылки (`notify`).
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.application.dto.search import ListingPreview
from app.application.search.use_cases import (
    PREVIEW_LIMIT_DEFAULT,
    PreviewListings,
    PreviewListingsQuery,
)
from app.domain.search.criteria import SearchCriteria
from app.infrastructure.db.repositories.listings_search import (
    SqlAlchemyListingSearchRepository,
    build_listing_query,
)

__all__ = [
    "build_listing_query",
    "preview_listings",
    "search_with_relaxation",
]


def _use_case(session: AsyncSession) -> PreviewListings:
    """Сценарий превью на текущей сессии."""
    return PreviewListings(SqlAlchemyListingSearchRepository(session))


async def preview_listings(
    session: AsyncSession,
    criteria: SearchCriteria,
    limit: int = PREVIEW_LIMIT_DEFAULT,
) -> list[ListingPreview]:
    """Объявления по точным критериям (без ослабления)."""
    outcome = await _use_case(session).execute(
        PreviewListingsQuery(criteria=criteria, limit=limit)
    )
    return outcome.items


async def search_with_relaxation(
    session: AsyncSession,
    criteria: SearchCriteria,
    limit: int = PREVIEW_LIMIT_DEFAULT,
) -> tuple[list[ListingPreview], list[str]]:
    """Поиск «точный → с ослаблением».

    Возвращает (объявления, список ослабленных параметров по-русски).
    Критерии пользователя не меняются — ослабление только для запроса.
    """
    outcome = await _use_case(session).execute(
        PreviewListingsQuery(criteria=criteria, limit=limit)
    )
    return outcome.items, list(outcome.relaxed)
