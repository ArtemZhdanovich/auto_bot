"""Порты AI-помощника админа: агрегаты панели и здоровье источников."""

from __future__ import annotations

from typing import Any, Protocol


class AdminStatsPort(Protocol):
    """Агрегаты админ-панели, которые помощник показывает модели."""

    async def leads_by_status(self) -> dict[str, int]:
        """Количество лидов по статусам."""

    async def partner_feedback(self) -> dict[str, int]:
        """Количество партнёрских фидбеков по значениям `partner_status`."""

    async def partners(self) -> list[dict[str, Any]]:
        """Партнёры: план, дневная квота, статус."""


class SourceHealthPort(Protocol):
    """Источники и их здоровье."""

    async def list_with_health(self) -> list[dict[str, Any]]:
        """Источники с ошибками подряд, латентностью и объёмом."""
