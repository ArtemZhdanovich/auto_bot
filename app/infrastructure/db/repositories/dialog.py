"""SQLAlchemy-реализация `DialogRepositoryPort`.

Здесь и только здесь живёт SQL диалога: архив `dialog_sessions`, фильтры
`filters` и список включённых источников для постановки задач парсинга.
Владение проверяется в запросе (`user_id` в WHERE), поэтому сценарий
получает `None` вместо чужой сессии — существование не раскрывается.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.dialog.dto import DialogSessionRecord
from app.domain.dialog.state import DialogState
from app.infrastructure.db.models import DialogSession, Source, UserFilter

logger = logging.getLogger(__name__)


def _to_record(row: DialogSession) -> DialogSessionRecord:
    """ORM → DTO архива (единственное место встречи)."""
    return DialogSessionRecord(
        session_id=str(row.id),
        user_id=row.user_id,
        status=row.status,
        criteria=dict(row.criteria or {}),
        turn_count=row.turn_count or 0,
        messages=tuple(row.messages or ()),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class SqlAlchemyDialogRepository:
    """Архив диалогов и фильтры на SQLAlchemy 2.0 async."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def archive(self, state: DialogState) -> None:
        """Сохранить/обновить сессию в текущей транзакции.

        Raises:
            ValueError: `session_id` состояния — не UUID.
        """
        session_id = uuid.UUID(state.session_id)
        existing = await self._session.get(DialogSession, session_id)
        if existing is None:
            existing = DialogSession(id=session_id)
            self._session.add(existing)
        existing.user_id = state.user_id
        existing.status = state.status
        existing.criteria = state.criteria.model_dump(mode="json")
        existing.turn_count = state.turn_count
        existing.messages = [m.model_dump(mode="json") for m in state.messages]
        existing.updated_at = datetime.now(UTC)
        if existing.created_at is None:
            existing.created_at = state.created_at
        if state.status in ("done", "searching"):
            existing.finished_at = datetime.now(UTC)

    async def add_filter(self, user_id: int, criteria: dict[str, Any]) -> None:
        """Создать фильтр пользователя по критериям диалога."""
        self._session.add(
            UserFilter(
                user_id=user_id,
                name=f"Поиск от {datetime.now(UTC):%d.%m.%Y %H:%M}",
                criteria=criteria,
            )
        )

    async def commit(self) -> None:
        """Зафиксировать транзакцию."""
        await self._session.commit()

    async def last_session(self, user_id: int) -> DialogSessionRecord | None:
        """Последняя архивная сессия пользователя."""
        result = await self._session.execute(
            select(DialogSession)
            .where(DialogSession.user_id == user_id)
            .order_by(
                DialogSession.updated_at.desc().nulls_last(),
                DialogSession.created_at.desc().nulls_last(),
            )
            .limit(1)
        )
        row = result.scalars().first()
        return _to_record(row) if row is not None else None

    async def owned_session(
        self, session_id: str, user_id: int
    ) -> DialogSessionRecord | None:
        """Сессия только для владельца: чужой/несуществующий id → `None`."""
        try:
            session_uuid = uuid.UUID(session_id)
        except (AttributeError, TypeError, ValueError):
            return None
        result = await self._session.execute(
            select(DialogSession)
            .where(
                DialogSession.id == session_uuid,
                DialogSession.user_id == user_id,
            )
            .limit(1)
        )
        row = result.scalars().first()
        return _to_record(row) if row is not None else None

    async def enabled_source_names(self) -> list[str]:
        """Имена включённых источников."""
        result = await self._session.execute(
            select(Source.name).where(Source.enabled.is_(True))
        )
        return list(result.scalars().all())
