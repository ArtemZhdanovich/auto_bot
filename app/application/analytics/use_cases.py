"""Сценарии аналитики (P1-7): запись события и дашборды воронки.

Запись события никогда не ломает основной поток: ошибка БД — warning в лог,
бизнес-операция продолжается (тот же принцип, что в alerting).
"""

from __future__ import annotations

import logging
from typing import Any

from app.application.admin.ports import (
    AnalyticsRepository,
    FunnelRepository,
)
from app.domain.analytics import funnels

logger = logging.getLogger(__name__)


class TrackEvent:
    """Записать событие воронки, не прерывая бизнес-операцию."""

    def __init__(self, analytics: AnalyticsRepository) -> None:
        self._analytics = analytics

    async def execute(
        self,
        user_id: int | None,
        name: str,
        props: dict[str, Any] | None = None,
    ) -> None:
        """Записать событие; любая ошибка остаётся в логе.

        Args:
            user_id: Пользователь события (None — анонимное действие).
            name: Каноническое имя из `app.domain.analytics.events`.
            props: Дополнительные свойства.
        """
        try:
            await self._analytics.add_event(
                user_id=user_id, name=name, props=props
            )
        except Exception:  # noqa: BLE001 — аналитика не ломает бизнес-поток
            logger.warning("analytics_track_failed: %s", name, exc_info=True)


class FunnelDashboard:
    """Воронка search→dialog→lead→subscription за окно."""

    def __init__(self, funnel: FunnelRepository) -> None:
        self._funnel = funnel

    async def execute(self, days: int = 30) -> list[funnels.FunnelStage]:
        """Этапы с конверсиями к предыдущему и первому.

        Args:
            days: Ширина окна в днях.

        Returns:
            Этапы в порядке воронки; пропущенный этап — с нулём.
        """
        names = [event for event, _label in funnels.FUNNEL_STAGES]
        rows = await self._funnel.users_by_event(names, days)
        return funnels.funnel_stages({row.name: row.users for row in rows})


class RetentionDashboard:
    """Когорты по дню регистрации: размер, D1/D7."""

    def __init__(self, funnel: FunnelRepository) -> None:
        self._funnel = funnel

    async def execute(self, days: int = 30) -> list[funnels.RetentionCohort]:
        """Когорты за окно, свежие дни первыми.

        Args:
            days: Ширина окна в днях.

        Returns:
            Когорты с абсолютными и процентными D1/D7.
        """
        rows = await self._funnel.cohorts(days)
        return funnels.retention_cohorts(
            [(row.day, row.cohort, row.d1, row.d7) for row in rows]
        )
