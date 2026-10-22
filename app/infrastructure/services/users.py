"""Фасад работы с пользователями: привязка сценария к сессии.

Upsert и SQL — в репозитории, событие регистрации — в use case; здесь
только совместимая сигнатура `upsert_user`.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.application.analytics.use_cases import TrackEvent
from app.application.dto.users import UserProfile
from app.application.users.use_cases import UpsertUser, UpsertUserCommand
from app.infrastructure.db.repositories.analytics import (
    SqlAlchemyAnalyticsRepository,
)
from app.infrastructure.db.repositories.users import SqlAlchemyUserRepository

__all__ = ["upsert_user"]


async def upsert_user(
    session: AsyncSession,
    *,
    telegram_id: int,
    username: str | None = None,
    first_name: str | None = None,
    last_name: str | None = None,
    language_code: str | None = None,
    source: str | None = None,
) -> UserProfile:
    """Создаёт пользователя или обновляет его данные из Telegram."""
    return await UpsertUser(
        SqlAlchemyUserRepository(session),
        TrackEvent(SqlAlchemyAnalyticsRepository(session)),
    ).execute(
        UpsertUserCommand(
            telegram_id=telegram_id,
            username=username,
            first_name=first_name,
            last_name=last_name,
            language_code=language_code,
            source=source,
        )
    )
