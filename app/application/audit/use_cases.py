"""Сценарий audit log админ-действий (UI/UX ТЗ §3.9, миграция 0008)."""

from __future__ import annotations

import logging
from typing import Any

from app.domain.audit.ports import AuditEntry, AuditRepository

logger = logging.getLogger(__name__)


class LogAdminAction:
    """Записать действие администратора, не ломая основной поток.

    Секреты (API-ключи) в `payload` не передаются — только префиксы.
    """

    def __init__(self, audit: AuditRepository) -> None:
        self._audit = audit

    async def execute(
        self,
        *,
        action: str,
        entity: str | None = None,
        entity_id: str | int | None = None,
        payload: dict[str, Any] | None = None,
        actor: str = "admin",
        result: str = "ok",
    ) -> None:
        """Записать действие; ошибка БД остаётся в логе.

        Args:
            action: Код действия (`partner.create`).
            entity: Тип сущности.
            entity_id: Идентификатор сущности (приводится к строке).
            payload: Детали без секретов.
            actor: Кто выполнил.
            result: Итог действия.
        """
        entry = AuditEntry(
            action=action,
            entity=entity,
            entity_id=str(entity_id) if entity_id is not None else None,
            payload=payload,
            actor=actor,
            result=result,
        )
        try:
            await self._audit.record(entry)
        except Exception as exc:  # noqa: BLE001 — audit не должен ломать API
            logger.error("admin_action_log_failed: %s", exc)
