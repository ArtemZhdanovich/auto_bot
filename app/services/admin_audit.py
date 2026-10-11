"""Audit log админ-действий (UI/UX ТЗ §3.9, миграция 0008)."""

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AdminAction

logger = logging.getLogger(__name__)


async def log_admin_action(
    session: AsyncSession,
    *,
    action: str,
    entity: str | None = None,
    entity_id: str | None = None,
    payload: dict | None = None,
    actor: str = "admin",
    result: str = "ok",
) -> None:
    """Записать действие в audit log. Ошибка логирования не ломает основной поток.

    Секреты (API-ключи) в payload не передавать — только префиксы.
    """
    try:
        session.add(
            AdminAction(
                actor=actor,
                action=action,
                entity=entity,
                entity_id=str(entity_id) if entity_id is not None else None,
                payload=payload,
                result=result,
            )
        )
        await session.commit()
    except Exception as exc:  # noqa: BLE001 — audit не должен ломать API
        logger.error("admin_action_log_failed: %s", exc)
