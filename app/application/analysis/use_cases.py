"""Сценарии AI-анализа цены: разбор по квоте и чтение кэша.

Владение проверяется здесь: пользователь берётся только из аутентификации
(`user_id` в команде), гость получает отказ, а квота окна считается по
ВСЕМ машинам пользователя (free — 1/неделю, pro — 30/день, business —
без лимита). Business дополнительно получает «глубокий» разбор: в контекст
LLM подмешивается выжимка чека осмотра.

LLM приходит через `LLMPort` (фабрика может бросить `AiUnavailable`);
`listing`/`user` — через порты, ORM в сценарий не попадает.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from app.application.catalog.dto import (
    GUEST_FORBIDDEN_MESSAGE,
    QUOTA_EXCEEDED_MESSAGE,
    AnalysisCommand,
    ListingSnapshot,
    PriceAnalysisResult,
)
from app.application.catalog.ports import LLMFactory, ListingReadRepository, UserLookupPort
from app.application.dialog.use_cases import AiUnavailable
from app.application.errors import Forbidden, NotFound
from app.application.pricing.use_cases import AnalyzePrice
from app.application.catalog.dto import AnalyzePriceCommand
from app.application.shared.ports import UnitOfWork
from app.domain.listings.ports import ListingRepository
from app.domain.pricing.ports import PriceAnalysisRepository
from app.domain.pricing.rules import (
    QuotaExceeded,
    quota_applies,
    quota_left,
    quota_window_days,
)
from app.domain.subscription.policies import TIERS

logger = logging.getLogger(__name__)


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


def _inspection_digest(report: object) -> str | None:
    """Выжимка чека осмотра для «глубокого» разбора (Business).

    Собирает сводку/оценку/примечания отметок — то, что объясняет отклонение
    цены от медианы рынка. `None` — чека нет.
    """
    parts: list[str] = []
    grade = getattr(report, "grade", None)
    if grade:
        parts.append(f"оценка: {grade}")
    if getattr(report, "summary", None):
        parts.append(str(report.summary)[:400])
    marks = getattr(report, "marks", None) or []
    notes = [str(m.get("note")) for m in marks if isinstance(m, dict) and m.get("note")]
    if notes:
        parts.append("повреждения: " + ", ".join(notes[:8]))
    return "; ".join(parts) if parts else None


class RunAnalysis:
    """Разбор цены: гость → квота окна тарифа → LLM/статистика → сохранение."""

    def __init__(
        self,
        users: UserLookupPort,
        listings: ListingRepository,
        analyses: PriceAnalysisRepository,
        llm_factory: LLMFactory,
        uow: UnitOfWork,
        reports: ListingReadRepository | None = None,
    ) -> None:
        self._users = users
        self._listings = listings
        self._analyses = analyses
        self._llm_factory = llm_factory
        self._uow = uow
        self._reports = reports

    async def execute(self, command: AnalysisCommand) -> PriceAnalysisResult:
        """Выполнить разбор.

        Raises:
            NotFound: Объявления или пользователя нет.
            Forbidden: Гость или исчерпана квота окна тарифа.
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
        left = await self._quota_left(command.user_id, tier)
        digest = await self._inspection_for_deep_analysis(command.listing_id, tier)
        llm = self._llm_factory()
        try:
            result = await AnalyzePrice(self._analyses, llm, self._uow).execute(
                AnalyzePriceCommand(
                    listing=_snapshot(card),
                    user_id=command.user_id,
                    tier=tier,
                    free_quota_left=left,
                    inspection_digest=digest,
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
            "analysis_completed user_id=%s listing_id=%s tier=%s deep=%s source=%s",
            command.user_id,
            command.listing_id,
            tier,
            digest is not None,
            command.channel,
        )
        return result

    async def _quota_left(self, user_id: int, tier: str) -> int | None:
        """Остаток разборов в окне тарифа (на все машины суммарно).

        `None` — тариф без лимита (business).
        """
        if not quota_applies(tier):
            return None
        used = await self._analyses.count_recent_user(
            user_id,
            since=datetime.now(UTC) - timedelta(days=quota_window_days(tier)),
        )
        return quota_left(used, tier)

    async def _inspection_for_deep_analysis(
        self, listing_id: int, tier: str
    ) -> str | None:
        """Чек осмотра в контекст LLM — только тарифам с доступом к чекам.

        Business-разбор «глубокий»: цена объясняется с учётом состояния
        кузова. Ошибки чтения чека не роняют разбор.
        """
        if self._reports is None or not TIERS.get(tier, TIERS["free"]).inspection:
            return None
        try:
            report = await self._reports.get_report(listing_id)
        except Exception:  # noqa: BLE001 — чек необязателен для разбора
            logger.warning("analysis_inspection_read_failed listing_id=%s", listing_id)
            return None
        return _inspection_digest(report) if report is not None else None


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
