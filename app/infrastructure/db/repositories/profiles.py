"""SQLAlchemy-реализация порта профиля.

`mark_onboarded` — атомарный `UPDATE ... WHERE onboarded_at IS NULL`:
двойной клик и параллельные запросы не перезаписывают первое значение
(важно для когорт «когда впервые прошёл онбординг»). После `commit`
объект `refresh`-ится: при `expire_on_commit=False` Core-UPDATE не меняет
in-memory атрибуты, и без `refresh` наружу ушло бы stale-значение.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.dto.profile import OnboardingResult, UserProfile
from app.infrastructure.db.models import User


class SqlAlchemyProfileRepository:
    """Профиль текущего пользователя: только публичные поля."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, user_id: int) -> UserProfile | None:
        """Профиль по `telegram_id` или `None`."""
        row = await self._session.get(User, user_id)
        return _to_profile(row) if row is not None else None

    async def mark_onboarded(self, user_id: int) -> OnboardingResult | None:
        """Идемпотентно проставить `onboarded_at`.

        Returns:
            Профиль и `first_time` (обновил ли этот вызов флаг), либо
            `None`, если пользователя нет.
        """
        result = await self._session.execute(
            update(User)
            .where(User.telegram_id == user_id)
            .where(User.onboarded_at.is_(None))
            .values(onboarded_at=datetime.now(UTC))
        )
        await self._session.commit()

        row = await self._session.get(User, user_id)
        if row is None:
            return None
        # `expire_on_commit=False` → атрибуты после Core-UPDATE устарели.
        await self._session.refresh(row)
        return OnboardingResult(
            profile=_to_profile(row), first_time=bool(result.rowcount)
        )


def _to_profile(row: User) -> UserProfile:
    """ORM → DTO. Секреты (`password_hash`, `email`) сюда не попадают."""
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
