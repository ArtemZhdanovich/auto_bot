"""SQLAlchemy-реализация порта лидов.

Вся работа с `leads` — здесь: FK-проверки, гонка по `idempotency_key`
(partial unique `uq_leads_idempotency_key`), блокировка строки для PATCH.
Сценарий получает готовые `LeadView` и не видит ни SQL, ни транзакций.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.partners.dto import (
    LeadPatch,
    LeadView,
)
from app.application.errors import Conflict
from app.infrastructure.db.models import Lead, Listing, User

logger = logging.getLogger(__name__)


class SqlAlchemyLeadRepository:
    """Репозиторий лидов на SQLAlchemy 2.0 async."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def user_exists(self, user_id: int) -> bool:
        """Есть ли пользователь с таким `telegram_id`."""
        return await self._session.get(User, user_id) is not None

    async def listing_exists(self, listing_id: int) -> bool:
        """Есть ли объявление с таким `id`."""
        return await self._session.get(Listing, listing_id) is not None

    async def find_by_idempotency_key(self, key: str) -> LeadView | None:
        """Лид, созданный с этим ключом, или `None`."""
        stmt = select(Lead).where(Lead.idempotency_key == key)
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return _to_view(row) if row is not None else None

    async def create(
        self,
        *,
        user_id: int,
        listing_id: int | None,
        source: str | None,
        comment: str | None,
        idempotency_key: str | None,
    ) -> LeadView:
        """Вставить лид; при гонке по ключу вернуть существующий.

        Raises:
            Conflict: Нарушено ограничение, не связанное с ключом, или
                строку с ключом не удалось перечитать.
        """
        lead = Lead(
            user_id=user_id,
            listing_id=listing_id,
            source=source,
            # Явный дефолт: колоночный `default=` срабатывает только при flush.
            status="new",
            comment=comment,
            idempotency_key=idempotency_key,
        )
        self._session.add(lead)
        try:
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            # Гонка: параллельный запрос вставил тот же idempotency_key.
            if idempotency_key:
                existing = await self.find_by_idempotency_key(idempotency_key)
                if existing is not None:
                    logger.info(
                        "lead_idempotent_race lead_id=%s key=%s",
                        existing.id, idempotency_key,
                    )
                    return existing
            raise Conflict("Lead already exists or conflicts") from exc

        await self._session.refresh(lead)
        return _to_view(lead)

    async def patch(self, lead_id: int, changes: LeadPatch) -> LeadView | None:
        """Применить патч под `SELECT ... FOR UPDATE`.

        Параллельные PATCH одного лида сериализуются, поэтому два менеджера
        не теряют изменения друг друга.

        Returns:
            Обновлённый лид или `None`, если лида нет.
        """
        stmt = select(Lead).where(Lead.id == lead_id).with_for_update()
        lead = (await self._session.execute(stmt)).scalar_one_or_none()
        if lead is None:
            return None

        if changes.status is not None:
            lead.status = changes.status
        if changes.manager_id is not None:
            lead.manager_id = changes.manager_id
        if changes.comment is not None:
            lead.comment = changes.comment
        lead.updated_at = datetime.now(UTC)
        await self._session.commit()
        await self._session.refresh(lead)
        return _to_view(lead)


def _to_view(row: Lead) -> LeadView:
    """ORM → DTO (единственное место, где они встречаются)."""
    return LeadView(
        id=row.id,
        user_id=row.user_id,
        listing_id=row.listing_id,
        source=row.source,
        status=row.status,
        manager_id=row.manager_id,
        comment=row.comment,
        created_at=row.created_at,
    )
