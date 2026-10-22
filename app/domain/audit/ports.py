"""Порт audit log админ-действий (UI/UX ТЗ §3.9, миграция 0008)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class AuditEntry:
    """Одно действие администратора.

    Секреты (API-ключи) в `payload` не передаются — только префиксы.
    """

    action: str
    entity: str | None = None
    entity_id: str | None = None
    payload: dict[str, Any] | None = None
    actor: str = "admin"
    result: str = "ok"


class AuditRepository(Protocol):
    """Хранилище audit log.

    Реализация пишет строку и коммитит сама: аудит вызывается после
    успешной бизнес-операции и своей транзакции не открывает.
    """

    async def record(self, entry: AuditEntry) -> None:
        """Записать действие; ошибку не глотает — политику задаёт use case."""
