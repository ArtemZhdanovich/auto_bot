"""Фасад дашбордов воронок (P2-бэклог №2): привязка сценариев к сессии.

SQL — в `app.infrastructure.db.repositories.funnels`, конверсии и проценты —
в `app.domain.analytics.funnels`; здесь только прежняя сигнатура
`funnel(session, days)` / `retention(session, days)` и форма ответа.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.application.analytics.use_cases import (
    FunnelDashboard,
    RetentionDashboard,
)
from app.infrastructure.db.repositories.funnels import SqlAlchemyFunnelRepository

__all__ = ["funnel", "retention"]


async def funnel(
    session: AsyncSession, days: int = 30
) -> list[dict[str, Any]]:
    """Воронка за окно: пользователи на каждом этапе + конверсии.

    conv_prev — к предыдущему этапу; conv_first — к первому (search).
    """
    stages = await FunnelDashboard(
        SqlAlchemyFunnelRepository(session)
    ).execute(days)
    return [asdict(stage) for stage in stages]


async def retention(
    session: AsyncSession, days: int = 30
) -> list[dict[str, Any]]:
    """Когорты по дню регистрации: размер, D1/D7 абсолютные и в процентах."""
    cohorts = await RetentionDashboard(
        SqlAlchemyFunnelRepository(session)
    ).execute(days)
    return [asdict(cohort) for cohort in cohorts]
