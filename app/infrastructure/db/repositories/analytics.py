"""SQLAlchemy-реализация записи событий воронки (P1-7)."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.db.models import AnalyticsEvent

logger = logging.getLogger(__name__)


class SqlAlchemyAnalyticsRepository:
    """Запись в analytics_events.

    Коммитит сама: событие пишется отдельной транзакцией, чтобы его сбой не
    откатывал бизнес-операцию. Политику «не ломать поток» задаёт use case —
    здесь только гигиена сессии.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_event(
        self,
        *,
        user_id: int | None,
        name: str,
        props: dict[str, Any] | None = None,
    ) -> None:
        """Добавить событие и зафиксировать его.

        Raises:
            Exception: Любая ошибка БД — после отката сессии, чтобы она не
                осталась в сломанном состоянии.
        """
        try:
            self._session.add(
                AnalyticsEvent(user_id=user_id, name=name, props=props)
            )
            await self._session.commit()
        except Exception:
            try:
                await self._session.rollback()
            except Exception:  # noqa: BLE001 — откат не должен скрыть причину
                pass
            raise
