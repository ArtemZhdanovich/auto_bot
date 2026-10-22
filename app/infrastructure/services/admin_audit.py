"""Фасад audit log админ-действий (UI/UX ТЗ §3.9, миграция 0008).

Сценарий и политика «audit не ломает поток» — в `app.application.audit`,
SQL — в репозитории; здесь только совместимая сигнатура.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.application.audit.use_cases import LogAdminAction
from app.infrastructure.db.repositories.audit import SqlAlchemyAuditRepository

__all__ = ["log_admin_action"]


async def log_admin_action(
    session: AsyncSession,
    *,
    action: str,
    entity: str | None = None,
    entity_id: str | int | None = None,
    payload: dict[str, Any] | None = None,
    actor: str = "admin",
    result: str = "ok",
) -> None:
    """Записать действие в audit log. Ошибка логирования не ломает основной поток.

    Секреты (API-ключи) в payload не передавать — только префиксы.
    """
    await LogAdminAction(SqlAlchemyAuditRepository(session)).execute(
        action=action,
        entity=entity,
        entity_id=entity_id,
        payload=payload,
        actor=actor,
        result=result,
    )
