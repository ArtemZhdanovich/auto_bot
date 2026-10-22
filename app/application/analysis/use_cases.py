"""Сценарии AI-анализа цены: разбор по квоте и чтение кэша.

Владение проверяется здесь: пользователь берётся только из аутентификации
(`user_id` в команде), гость получает отказ, а квота free-tier считается по
паре (user_id, listing_id) — «чужой» разбор не подмешивается.

LLM приходит через `LLMPort` (фабрика может бросить `AiUnavailable`);
`listing`/`user` — через порты, ORM в сценарий не попадает.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Protocol

from app.application.dto.analysis import (
    GUEST_FORBIDDEN_MESSAGE,
    QUOTA_EXCEEDED_MESSAGE,
    AnalysisCommand,
)
from app.application.dto.dialog import UserProfile
from app.application.dto.pricing import ListingSnapshot, PriceAnalysisResult
from app.application.dialog.use_cases import AiUnavailable
from app.application.errors import Forbidden, NotFound
from app.application.ports import LLMPort
from app.application.pricing.use_cases import AnalyzePrice, AnalyzePriceCommand
from app.application.shared.ports import UnitOfWork
from app.domain.listings.ports import ListingRepository
from app.domain.pricing.ports import PriceAnalysisRepository
from app.domain.pricing.rules import (
    FREE_QUOTA_WINDOW_DAYS,
    QuotaExceeded,
    quota_applies,
    quota_left,
)

logger = logging.getLogger(__name__)


class UserLookupPort(Protocol):
    """Профиль пользователя для сценария анализа."""

    async def get(self, user_id: int) -> UserProfile | None:
        """Профиль или `None`."""


class LLMFactory(Protocol):
    """Фабрика LLM-клиента (`LLMPort`)."""

    def __call__(self) -> LLMPort:
        """Создать/получить клиент.

        Raises:
            AiUnavailable: Провайдер не сконфигурирован.
        """


def _snapshot(card: object) -> ListingSnapshot:
    """`ListingCard` → `ListingSnapshot` (единственное место маппинга)."""
    return ListingSnapshot(
        id=card.id,
        make=card.make,
        model=card.model,
        year=card.year,
        price=float(card.price) if card.price is not None else None,
        currency=card.currency,
        mileage_km=card.mileage_km,
    )


class RunAnalysis:
    """Разбор цены: гость → квота → LLM/статистика → сохранение."""

    def __init__(
        self,
        users: UserLookupPort,
        listings: ListingRepository,
        analyses: PriceAnalysisRepository,
        llm_factory: LLMFactory,
        uow: UnitOfWork,
    ) -> None:
        self._users = users
        self._listings = listings
        self._analyses = analyses
        self._llm_factory = llm_factory
        self._uow = uow

    async def execute(self, command: AnalysisCommand) -> PriceAnalysisResult:
        """Выполнить разбор.

        Raises:
            NotFound: Объявления или пользователя нет.
            Forbidden: Гость или исчерпана бесплатная квота.
            AiUnavailable: LLM не сконфигурирована.
        """
        cards = await self._listings.cards_by_ids([command.listing_id])
        card = cards.get(command.listing_id)
        if card is None:
            raise NotFound("Listing not found")

        profile = await self._users.get(command.user_id)
        if profile is None:
            raise NotFound("User not found")
        if not profile.is_active:
            raise Forbidden("User is deactivated")
        if profile.is_guest:
            logger.info(
                "analysis_guest_forbidden user_id=%s listing_id=%s source=%s",
                command.user_id,
                command.listing_id,
                command.channel,
            )
            raise Forbidden(GUEST_FORBIDDEN_MESSAGE)

        tier = profile.tier
        left = await self._free_quota_left(command) if quota_applies(tier) else None
        llm = self._llm_factory()
        try:
            result = await AnalyzePrice(self._analyses, llm, self._uow).execute(
                AnalyzePriceCommand(
                    listing=_snapshot(card),
                    user_id=command.user_id,
                    tier=tier,
                    free_quota_left=left,
                )
            )
        except QuotaExceeded as exc:
            logger.info(
                "analysis_quota_exceeded user_id=%s listing_id=%s tier=%s source=%s",
                command.user_id,
                command.listing_id,
                tier,
                command.channel,
            )
            raise Forbidden(QUOTA_EXCEEDED_MESSAGE) from exc

        logger.info(
            "analysis_completed user_id=%s listing_id=%s tier=%s source=%s",
            command.user_id,
            command.listing_id,
            tier,
            command.channel,
        )
        return result

    async def _free_quota_left(self, command: AnalysisCommand) -> int:
        """Сколько бесплатных разборов этой машины осталось (окно 7 дней)."""
        used = await self._analyses.count_recent(
            command.user_id,
            command.listing_id,
            since=datetime.now(UTC) - timedelta(days=FREE_QUOTA_WINDOW_DAYS),
        )
        return quota_left(used)


class GetLatestAnalysis:
    """Кэш последнего разбора листинга (общий на объявление)."""

    def __init__(
        self,
        listings: ListingRepository,
        analyses: PriceAnalysisRepository,
    ) -> None:
        self._listings = listings
        self._analyses = analyses

    async def execute(self, listing_id: int) -> dict | None:
        """Payload последнего разбора или `None`.

        Raises:
            NotFound: Объявления нет.
        """
        cards = await self._listings.cards_by_ids([listing_id])
        if listing_id not in cards:
            raise NotFound("Listing not found")
        return await self._analyses.latest_payload(listing_id)


__all__ = [
    "AiUnavailable",
    "GetLatestAnalysis",
    "LLMFactory",
    "RunAnalysis",
    "UserLookupPort",
]
