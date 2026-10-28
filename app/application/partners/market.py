"""Анализ рынка для партнёрского контура (прод-ревизия 2026-10).

Сводка по марке/модели (объём, медиана/мин/макс) + «выгодные варианты» —
объявления ниже медианы рынка (deal). Инструмент сотрудников партнёров:
обычным пользователям недоступен (отдельная поверхность /partner).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

logger = logging.getLogger(__name__)


class PartnerMarketRepository(Protocol):
    """Порт рыночной аналитики для партнёров."""

    async def report(
        self,
        *,
        make: str | None = None,
        model: str | None = None,
        deals_limit: int = 10,
    ) -> "MarketReport":
        """Сводка рынка + выгодные лоты."""


@dataclass(frozen=True, slots=True)
class MarketSummary:
    """Сводка рынка по марке/модели."""

    make: str | None = None
    model: str | None = None
    count: int = 0
    min_price: float | None = None
    max_price: float | None = None
    median_price: float | None = None


@dataclass(frozen=True, slots=True)
class MarketReport:
    """Отчёт рынка для партнёра: сводка + выгодные лоты."""

    summary: MarketSummary = field(default_factory=MarketSummary)
    deals: list[dict[str, Any]] = field(default_factory=list)


class GetPartnerMarket:
    """Рынок и выгодные варианты (партнёрский API-ключ)."""

    def __init__(self, market_repo: PartnerMarketRepository) -> None:
        self._market = market_repo

    async def execute(
        self, make: str | None = None, model: str | None = None, deals_limit: int = 10
    ) -> MarketReport:
        """Сводка + лоты дешевле 93% медианы (сортировка по выгоде)."""
        return await self._market.report(make=make, model=model, deals_limit=deals_limit)
