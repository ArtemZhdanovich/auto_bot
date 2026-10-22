"""Фасад продуктовой аналитики: привязка сценариев к сессии.

Имена событий — доменные (`app.domain.analytics.events`), запись — в
репозитории, политика «событие не ломает поток» — в use case. Модуль
сохраняет прежний API `track(...)` и набор `EVENT_*`.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.application.analytics.use_cases import TrackEvent
from app.domain.analytics.events import (
    EVENT_AI_SEARCH,
    EVENT_DIALOG_MESSAGE,
    EVENT_LEAD_CREATED,
    EVENT_LEAD_FEEDBACK,
    EVENT_LEAD_SOLD,
    EVENT_SEARCH_COMPLETED,
    EVENT_SUBSCRIPTION_ACTIVATED,
    EVENT_USER_REGISTERED,
)
from app.infrastructure.db.repositories.analytics import (
    SqlAlchemyAnalyticsRepository,
)

__all__ = [
    "EVENT_AI_SEARCH",
    "EVENT_DIALOG_MESSAGE",
    "EVENT_LEAD_CREATED",
    "EVENT_LEAD_FEEDBACK",
    "EVENT_LEAD_SOLD",
    "EVENT_SEARCH_COMPLETED",
    "EVENT_SUBSCRIPTION_ACTIVATED",
    "EVENT_USER_REGISTERED",
    "track",
]


async def track(
    session: AsyncSession,
    user_id: int | None,
    name: str,
    props: dict[str, Any] | None = None,
) -> None:
    """Записать событие воронки. Ошибка записи не бросается наружу."""
    await TrackEvent(SqlAlchemyAnalyticsRepository(session)).execute(
        user_id, name, props
    )
