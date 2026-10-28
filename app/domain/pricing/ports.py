"""Порт и данные разбора цены: то, что прикладному слою нужно от БД."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class ListingQuery:
    """Минимум полей объявления для подбора сопоставимых."""

    id: int
    make: str | None = None
    model: str | None = None
    year: int | None = None


@dataclass(frozen=True, slots=True)
class MarketContext:
    """Рыночный контекст: цены сопоставимых и история цены объявления."""

    comp_prices: tuple[float, ...] = ()
    history: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True, slots=True)
class PriceAnalysisRecord:
    """Строка разбора для сохранения (кэш общий на листинг)."""

    listing_id: int
    user_id: int
    verdict: str
    fair_min: float | None
    fair_max: float | None
    summary: str | None
    payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class AnalysisHistoryItem:
    """Строка истории «Мои анализы»: разбор + карточка машины."""

    id: int
    listing_id: int
    verdict: str
    fair_min: float | None
    fair_max: float | None
    summary: str | None
    created_at: datetime
    make: str | None = None
    model: str | None = None
    year: int | None = None
    price: float | None = None
    currency: str | None = None
    image: str | None = None


class PriceAnalysisRepository(Protocol):
    """Хранилище разборов цены и рыночного контекста объявления."""

    async def count_recent(
        self, user_id: int, listing_id: int, *, since: datetime
    ) -> int:
        """Сколько разборов этой машины юзер запросил с `since`."""

    async def count_recent_user(
        self, user_id: int, *, since: datetime
    ) -> int:
        """Сколько разборов ЛЮБЫХ машин юзер запросил с `since` (квота окна)."""

    async def list_for_user(
        self, user_id: int, *, limit: int = 50
    ) -> list[AnalysisHistoryItem]:
        """История разборов пользователя («Мои анализы»)."""

    async def context(self, listing: ListingQuery) -> MarketContext:
        """Сопоставимые цены и история цены объявления."""

    async def save(self, record: PriceAnalysisRecord) -> None:
        """Поставить строку разбора в текущую транзакцию."""

    async def latest_payload(self, listing_id: int) -> dict[str, Any] | None:
        """Payload последнего разбора листинга (общий кэш)."""
