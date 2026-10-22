"""SQLAlchemy-реализация порта фильтров пользователя.

Каждый метод принимает `user_id` и фильтрует по нему: чужой фильтр
неотличим от отсутствующего (сценарий отдаёт 404).
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.catalog.dto import (
    FilterDraft,
    FilterPatch,
    FilterRecord,
)
from app.infrastructure.db.models import User, UserFilter


def _to_record(row: UserFilter) -> FilterRecord:
    """ORM → DTO фильтра."""
    return FilterRecord(
        id=row.id,
        name=row.name,
        criteria=row.criteria or {},
        notify_mode=row.notify_mode or "digest",
        quiet_hours=row.quiet_hours,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class SqlAlchemyFilterRepository:
    """Репозиторий фильтров на SQLAlchemy 2.0 async."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_for_user(
        self, user_id: int, *, limit: int, offset: int
    ) -> list[FilterRecord]:
        """Фильтры пользователя по `created_at`."""
        rows = (
            await self._session.execute(
                select(UserFilter)
                .where(UserFilter.user_id == user_id)
                .order_by(UserFilter.created_at)
                .limit(limit)
                .offset(offset)
            )
        ).scalars().all()
        return [_to_record(row) for row in rows]

    async def create(
        self,
        user_id: int,
        draft: FilterDraft,
        *,
        max_filters: int | None,
    ) -> FilterRecord | None:
        """Создать фильтр; `None` — исчерпан лимит тарифа.

        Строка пользователя блокируется (`FOR UPDATE`), затем считается
        `COUNT(*)`: параллельные POST не превышают `max_filters`.
        """
        await self._session.execute(
            select(User.telegram_id)
            .where(User.telegram_id == user_id)
            .with_for_update()
        )
        if max_filters is not None:
            count = (
                await self._session.execute(
                    select(func.count())
                    .select_from(UserFilter)
                    .where(UserFilter.user_id == user_id)
                )
            ).scalar_one()
            if count >= max_filters:
                return None

        row = UserFilter(
            user_id=user_id,
            name=draft.name,
            criteria=draft.criteria,
            notify_mode=draft.notify_mode,
            quiet_hours=draft.quiet_hours,
            updated_at=datetime.now(UTC),
        )
        self._session.add(row)
        await self._session.flush()
        return _to_record(row)

    async def get_owned(
        self, user_id: int, filter_id: int
    ) -> FilterRecord | None:
        """Фильтр владельца или None."""
        row = await self._session.get(UserFilter, filter_id)
        if row is None or row.user_id != user_id:
            return None
        return _to_record(row)

    async def update_owned(
        self,
        user_id: int,
        filter_id: int,
        patch: FilterPatch,
        *,
        updated_at: datetime,
    ) -> FilterRecord | None:
        """Применить патч к фильтру владельца (None — нет/чужой)."""
        row = await self._session.get(UserFilter, filter_id)
        if row is None or row.user_id != user_id:
            return None

        if patch.name is not None:
            row.name = patch.name
        if patch.criteria is not None:
            row.criteria = patch.criteria
        if patch.notify_mode is not None:
            row.notify_mode = patch.notify_mode
        if patch.quiet_hours is not None:
            row.quiet_hours = patch.quiet_hours
        row.updated_at = updated_at
        await self._session.flush()
        return _to_record(row)

    async def delete_owned(self, user_id: int, filter_id: int) -> bool:
        """Удалить фильтр владельца; `False` — нет/чужой."""
        row = await self._session.get(UserFilter, filter_id)
        if row is None or row.user_id != user_id:
            return False
        await self._session.delete(row)
        return True
