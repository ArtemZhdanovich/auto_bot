"""Сценарии поиска: превью с ослаблением критериев.

Ослабление — доменное правило (`app.domain.search.query.relaxed`); здесь
только порядок шагов и обращения к порту.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.application.dto.search import PreviewOutcome
from app.application.search.ports import ListingSearchRepository
from app.domain.search.criteria import SearchCriteria
from app.domain.search.query import RELAX_STEPS, relaxed

PREVIEW_LIMIT_DEFAULT = 3


@dataclass(frozen=True, slots=True)
class PreviewListingsQuery:
    """Вход сценария превью."""

    criteria: SearchCriteria
    limit: int = PREVIEW_LIMIT_DEFAULT


class PreviewListings:
    """Поиск «точный → с ослаблением».

    Критерии пользователя не меняются — ослабляется только копия для
    запроса, поэтому повторный вызов сценария даёт тот же старт.
    """

    def __init__(self, listings: ListingSearchRepository) -> None:
        self._listings = listings

    async def execute(self, query: PreviewListingsQuery) -> PreviewOutcome:
        """Вернуть объявления и список ослабленных параметров.

        Args:
            query: Критерии и лимит.

        Returns:
            Пустой `relaxed`, если хватило точных критериев.
        """
        items = await self._listings.preview(query.criteria, query.limit)
        if items:
            return PreviewOutcome(items=items)

        dropped: list[str] = []
        current = query.criteria
        for step, label in RELAX_STEPS:
            candidate = relaxed(current, step)
            if candidate == current:
                continue  # параметра не было — ослаблять нечего
            current = candidate
            dropped.append(label)
            items = await self._listings.preview(current, query.limit)
            if items:
                break
        return PreviewOutcome(items=items, relaxed=tuple(dropped))
