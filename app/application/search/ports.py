"""Порт поиска по объявлениям."""

from __future__ import annotations

from typing import Protocol

from app.application.dto.search import ListingPreview
from app.domain.search.criteria import SearchCriteria


class ListingSearchRepository(Protocol):
    """Чтение объявлений по критериям (только превью)."""

    async def preview(
        self, criteria: SearchCriteria, limit: int = 3
    ) -> list[ListingPreview]:
        """До `limit` объявлений, свежие первыми."""
