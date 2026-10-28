"""Тонкий фасад AI-анализа стоимости: сборка сценария и совместимые имена.

Вердикты и квота — `domain/pricing/rules.py`, шаги разбора —
`application/pricing/use_cases.py`, SQL — `repositories/price_analysis.py`.

Квота окна зависит от тарифа: free — 1 разбор в неделю на все машины,
pro — 30 в день, business — без лимита.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.application.catalog.dto import AnalyzePriceCommand, ListingSnapshot
from app.application.pricing.use_cases import (
    AnalyzePrice,
    parse_verdict_json,
)
from app.application.shared.ports import LLMPort
from app.domain.pricing.rules import (
    QuotaExceeded,
    market_stats,
    quota_applies,
    quota_left as free_quota_left,
    quota_window_days,
    verdict_from_stats,
)
from app.infrastructure.db.models import Listing
from app.infrastructure.db.repositories.price_analysis import (
    SqlAlchemyPriceAnalysisRepository,
)
from app.infrastructure.db.uow import SessionUnitOfWork

# Прежние имена сервиса: их импортируют тесты и роутеры.
_stats = market_stats
_verdict_from_stats = verdict_from_stats
parse_llm_json = parse_verdict_json
QUOTA_WINDOW_DAYS = quota_window_days("free")

__all__ = [
    "QUOTA_WINDOW_DAYS",
    "QuotaExceeded",
    "latest_analysis",
    "parse_llm_json",
    "quota_left",
    "run_analysis",
]


async def quota_left(session: AsyncSession, user_id: int, tier: str = "free") -> int:
    """Сколько разборов осталось в окне тарифа (на все машины суммарно)."""
    repo = SqlAlchemyPriceAnalysisRepository(session)
    used = await repo.count_recent_user(
        user_id,
        since=datetime.now(UTC) - timedelta(days=quota_window_days(tier)),
    )
    return free_quota_left(used, tier)


async def run_analysis(
    session: AsyncSession,
    llm: LLMPort,
    listing: Listing,
    user_id: int,
    *,
    tier: str = "free",
) -> dict[str, Any]:
    """Полный разбор: квота → LLM/статистика → сохранить → payload для UI."""
    # Остаток считает фасад (модульная `quota_left` — точка подмены в тестах),
    # а решение «квота исчерпана» — доменное правило внутри сценария.
    left = (
        await quota_left(session, user_id, tier)
        if quota_applies(tier)
        else None
    )
    use_case = AnalyzePrice(
        SqlAlchemyPriceAnalysisRepository(session),
        llm,
        SessionUnitOfWork(session),
    )
    result = await use_case.execute(
        AnalyzePriceCommand(
            listing=ListingSnapshot(
                id=listing.id,
                make=listing.make,
                model=listing.model,
                year=listing.year,
                price=listing.price,
                currency=listing.currency,
                mileage_km=listing.mileage_km,
            ),
            user_id=user_id,
            tier=tier,
            free_quota_left=left,
        )
    )
    return result.to_payload()


async def latest_analysis(
    session: AsyncSession, listing_id: int
) -> dict[str, Any] | None:
    """Кэш последнего разбора листинга (общий для всех юзеров)."""
    return await SqlAlchemyPriceAnalysisRepository(session).latest_payload(listing_id)
