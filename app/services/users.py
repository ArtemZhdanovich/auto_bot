"""Сервисные функции работы с пользователями."""

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User
from app.services.analytics import EVENT_USER_REGISTERED, track

logger = logging.getLogger(__name__)


async def upsert_user(
    session: AsyncSession,
    *,
    telegram_id: int,
    username: str | None = None,
    first_name: str | None = None,
    last_name: str | None = None,
    language_code: str | None = None,
    source: str | None = None,
) -> User:
    """Создаёт пользователя или обновляет его данные из Telegram."""
    user = await session.get(User, telegram_id)
    is_new = user is None
    if user is None:
        user = User(telegram_id=telegram_id)
        session.add(user)

    user.username = username
    user.first_name = first_name
    user.last_name = last_name
    if language_code:
        user.language_code = language_code

    await session.commit()
    await session.refresh(user)
    if is_new:  # воронка: событие только при первой регистрации
        await track(session, telegram_id, EVENT_USER_REGISTERED, {"source": source})
    return user
