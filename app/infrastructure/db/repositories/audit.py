"""SQLAlchemy-реализация audit log админ-действий (миграция 0008)."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.audit.ports import AuditEntry
from app.infrastructure.db.models import AdminAction


class SqlAlchemyAuditRepository:
    """Запись в admin_actions.

    Коммитит сама: аудит вызывается после успешной операции и не открывает
    собственную транзакцию вызывающего.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(self, entry: AuditEntry) -> None:
        """Записать действие и зафиксировать строку."""
        self._session.add(
            AdminAction(
                actor=entry.actor,
                action=entry.action,
                entity=entry.entity,
                entity_id=entry.entity_id,
                payload=entry.payload,
                result=entry.result,
            )
        )
        await self._session.commit()
