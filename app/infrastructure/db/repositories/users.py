"""SQLAlchemy-реализация порта пользователей."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.application.identity.dto import (
    UpsertOutcome,
    UserProfile,
)
from app.infrastructure.db.models import User


class SqlAlchemyUserRepository:
    """Upsert профиля из Telegram.

    Коммитит и обновляет объект сама: вызывающему нужны серверные дефолты
    (`role`, `created_at`), а не «грязный» ORM-объект. Профильные поля
    обновляются, но `is_active` не трогается — забаненный пользователь не
    «разбанивается» ботом.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert(
        self,
        *,
        telegram_id: int,
        username: str | None = None,
        first_name: str | None = None,
        last_name: str | None = None,
        language_code: str | None = None,
    ) -> UpsertOutcome:
        """Создать или обновить пользователя.

        Returns:
            Профиль и `created` — была ли это первая регистрация.
        """
        user = await self._session.get(User, telegram_id)
        created = user is None
        if user is None:
            user = User(telegram_id=telegram_id)
            self._session.add(user)

        user.username = username
        user.first_name = first_name
        user.last_name = last_name
        if language_code:
            user.language_code = language_code

        await self._session.commit()
        await self._session.refresh(user)
        return UpsertOutcome(profile=_to_profile(user), created=created)


def _to_profile(row: User) -> UserProfile:
    """ORM → DTO (единственное место, где они встречаются)."""
    return UserProfile(
        telegram_id=row.telegram_id,
        username=row.username,
        first_name=row.first_name,
        last_name=row.last_name,
        language_code=row.language_code,
        role=row.role,
        subscription_tier=row.subscription_tier,
        subscription_until=row.subscription_until,
        created_at=row.created_at,
        is_active=row.is_active,
        onboarded_at=row.onboarded_at,
    )
