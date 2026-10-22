"""SQLAlchemy-репозиторий метрик мониторинга (ТЗ п. 10).

Число запросов постоянное и не зависит от количества строк: одна агрегатная
выборка на сущность плюс две группировки и один список здоровья источников.
N+1 здесь нет — по каждой строке дополнительных запросов не делается.

Сбой отдельной метрики не роняет `/metrics`: репозиторий логирует ошибку и
возвращает `None`, а 503 отдаёт только недоступная БД (`db_available`).
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.db.models import (
    AnalyticsEvent,
    Lead,
    Listing,
    PriceAnalysis,
    SentLog,
    SourceHealth,
    User,
    UserFilter,
)

logger = logging.getLogger(__name__)


class SqlAlchemyMetricsRepository:
    """Агрегаты для снимка метрик на SQLAlchemy 2.0 async."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def db_available(self) -> bool:
        """`True`, если БД отвечает на `SELECT 1`; `False` — недоступна."""
        try:
            await self._session.execute(text("SELECT 1"))
            return True
        except Exception:  # noqa: BLE001 — проба не бросает
            logger.exception("metrics_db_probe_failed")
            return False

    async def user_totals(self, since: datetime) -> tuple[int | None, int | None]:
        """Пользователи: всего и созданные после `since`."""
        return await self._totals(
            select(func.count(), func.count().filter(User.created_at >= since))
            .select_from(User)
        )

    async def user_filter_total(self) -> int | None:
        """Всего пользовательских фильтров."""
        return await self._scalar(select(func.count()).select_from(UserFilter))

    async def listing_totals(
        self, since: datetime
    ) -> tuple[int | None, int | None]:
        """Объявления: всего и впервые увиденные после `since`.

        `first_seen_at`, а не `updated_at`: свежие объявления не
        «перезаписываются» обновлением.
        """
        return await self._totals(
            select(
                func.count(),
                func.count().filter(Listing.first_seen_at >= since),
            ).select_from(Listing)
        )

    async def lead_totals(self, since: datetime) -> tuple[int | None, int | None]:
        """Лиды: всего и созданные после `since`."""
        return await self._totals(
            select(func.count(), func.count().filter(Lead.created_at >= since))
            .select_from(Lead)
        )

    async def price_analysis_totals(
        self, since: datetime
    ) -> tuple[int | None, int | None]:
        """Ценовые разборы: всего и созданные после `since`."""
        return await self._totals(
            select(
                func.count(),
                func.count().filter(PriceAnalysis.created_at >= since),
            ).select_from(PriceAnalysis)
        )

    async def notifications_sent(self, since: datetime) -> int | None:
        """Отправленные уведомления после `since`."""
        return await self._scalar(
            select(func.count())
            .select_from(SentLog)
            .where(SentLog.sent_at >= since)
        )

    async def users_by_tier(self) -> dict[str, int]:
        """Распределение пользователей по тарифам (`None` → "unknown")."""
        try:
            rows = (
                await self._session.execute(
                    select(User.subscription_tier, func.count())
                    .select_from(User)
                    .group_by(User.subscription_tier)
                )
            ).all()
        except Exception:  # noqa: BLE001 — метрика не должна ронять /metrics
            logger.exception("metrics_users_by_tier_failed")
            return {}
        return {(tier or "unknown"): int(count) for tier, count in rows}

    async def events_since(self, since: datetime, limit: int) -> dict[str, int]:
        """Top-N событий после `since` (кардинальность имён не ограничена)."""
        try:
            rows = (
                await self._session.execute(
                    select(AnalyticsEvent.name, func.count())
                    .select_from(AnalyticsEvent)
                    .where(AnalyticsEvent.created_at >= since)
                    .group_by(AnalyticsEvent.name)
                    .order_by(func.count().desc())
                    .limit(limit)
                )
            ).all()
        except Exception:  # noqa: BLE001
            logger.exception("metrics_events_failed")
            return {}
        return {name: int(count) for name, count in rows}

    async def sources_health(self) -> list[dict[str, Any]]:
        """Здоровье источников одним запросом (без N+1)."""
        try:
            rows = (
                await self._session.execute(select(SourceHealth))
            ).scalars().all()
        except Exception:  # noqa: BLE001
            logger.exception("metrics_sources_failed")
            return []
        return [
            {
                "source_name": health.source_name,
                "avg_latency_ms": health.avg_latency_ms,
                "error_streak": health.error_streak,
                "last_success_at": health.last_success_at,
                "last_error_at": health.last_error_at,
            }
            for health in rows
        ]

    async def _totals(self, stmt: Any) -> tuple[int | None, int | None]:
        """Пара COUNT (всего, за окно) одним запросом."""
        try:
            row = (await self._session.execute(stmt)).first()
        except Exception:  # noqa: BLE001 — одна сломанная метрика не валит ответ
            logger.exception("metrics_query_failed")
            return (None, None)
        if row is None:
            # Агрегат без GROUP BY всегда возвращает строку; пусто — заглушка.
            return (0, 0)
        return (int(row[0] or 0), int(row[1] or 0))

    async def _scalar(self, stmt: Any) -> int | None:
        """Один COUNT; при ошибке — `None` + лог."""
        try:
            value = (await self._session.execute(stmt)).scalar_one()
        except Exception:  # noqa: BLE001
            logger.exception("metrics_query_failed")
            return None
        return int(value) if value is not None else None
