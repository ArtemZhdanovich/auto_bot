"""История «Мои анализы»: разборы пользователя с карточками машин.

СТАБ (прод-ревизия 2026-10): полная реализация — см. задачи по вкладке
«Мои анализы»; контракт зафиксирован в DI и транспортном слое.
"""

from __future__ import annotations

from app.domain.pricing.ports import AnalysisHistoryItem, PriceAnalysisRepository


class ListMyAnalyses:
    """Разборы пользователя (последние `limit`), новые сверху."""

    def __init__(self, analyses: PriceAnalysisRepository) -> None:
        self._analyses = analyses

    async def execute(self, user_id: int, limit: int = 50) -> list[AnalysisHistoryItem]:
        """История разборов пользователя.

        Raises:
            NotFound: Пользователя нет (проверка транспорта).
        """
        return await self._analyses.list_for_user(user_id, limit=limit)
