"""Порты аналитики: запись событий и агрегаты дашбордов."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol

from app.application.dto.analytics import CohortRow, EventCount


class AnalyticsRepository(Protocol):
    """Запись событий воронки."""

    async def add_event(
        self,
        *,
        user_id: int | None,
        name: str,
        props: dict[str, Any] | None = None,
    ) -> None:
        """Записать событие; реализация коммитит сама, ошибку пробрасывает."""


class FunnelRepository(Protocol):
    """Read-only агрегаты по analytics_events (без ПД, только счётчики)."""

    async def users_by_event(
        self, names: Sequence[str], days: int
    ) -> list[EventCount]:
        """Distinct-пользователи по каждому событию за окно `days`."""

    async def cohorts(self, days: int) -> list[CohortRow]:
        """Когорты по дню регистрации за окно `days` (D1/D7)."""
