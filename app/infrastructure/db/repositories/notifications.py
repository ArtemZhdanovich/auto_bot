"""SQLAlchemy-реализация порта настроек уведомлений.

Bulk-обновление фильтров одним `UPDATE ... WHERE user_id = ?` (не N UPDATE
через ORM). `updated_at` ставит репозиторий: сценарий передаёт только
смысловые поля, чтобы «пустой патч» не выглядел изменением.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.notifications.settings import NotifyTarget
from app.infrastructure.db.models import User, UserFilter


class SqlAlchemyNotificationSettingsRepository:
    """Настройки уведомлений пользователя на SQLAlchemy 2.0 async."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def target(self, user_id: int) -> NotifyTarget | None:
        """Пользователь с полями тарифа или `None`."""
        user = await self._session.get(User, user_id)
        if user is None:
            return None
        return NotifyTarget(
            user_id=user.telegram_id,
            is_active=user.is_active,
            subscription_tier=user.subscription_tier,
            subscription_until=user.subscription_until,
        )

    async def update_filters(
        self,
        user_id: int,
        *,
        notify_mode: str | None = None,
        quiet_hours: str | None = None,
        clear_quiet_hours: bool = False,
    ) -> int:
        """Применить настройки ко всем фильтрам пользователя.

        Returns:
            Число обновлённых строк (0 — фильтров нет).
        """
        values: dict[str, object] = {}
        if notify_mode is not None:
            values["notify_mode"] = notify_mode
        if clear_quiet_hours:
            values["quiet_hours"] = None
        elif quiet_hours is not None:
            values["quiet_hours"] = quiet_hours
        values["updated_at"] = datetime.now(UTC)

        result = await self._session.execute(
            update(UserFilter)
            .where(UserFilter.user_id == user_id)
            .values(**values)
        )
        await self._session.commit()
        return result.rowcount or 0
