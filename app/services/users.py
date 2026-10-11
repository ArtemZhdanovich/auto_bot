"""Сервисные функции работы с пользователями."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User


async def upsert_user(
    session: AsyncSession,
    *,
    telegram_id: int,
    username: str | None = None,
    first_name: str | None = None,
    last_name: str | None = None,
    language_code: str | None = None,
) -> User:
    """Создаёт пользователя или обновляет его данные из Telegram."""
    user = await session.get(User, telegram_id)
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
    return user
