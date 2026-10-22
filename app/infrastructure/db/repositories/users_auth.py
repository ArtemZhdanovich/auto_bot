"""SQLAlchemy-реализация порта авторизации: найти / создать / обновить.

Отличие от `repositories/users.py`: тот обслуживает upsert профиля из Telegram
(бот), а здесь нужен поиск по email и хеш пароля для веб-входа. Коммит и
`refresh` — внутри методов: наружу отдаётся DTO, а не ORM-объект.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.auth.errors import DuplicateUserId, EmailConflict
from app.application.dto.auth import AuthUser
from app.application.dto.users import UpsertOutcome, UserProfile
from app.infrastructure.db.models import User
from app.infrastructure.db.repositories.users import _to_profile


class SqlAlchemyAuthUserRepository:
    """Пользователи для сценариев авторизации."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def find(self, telegram_id: int) -> UserProfile | None:
        """Найти профиль по `telegram_id`."""
        user = await self._session.get(User, telegram_id)
        return None if user is None else _to_profile(user)

    async def find_by_email(self, email: str) -> AuthUser | None:
        """Найти аккаунт по нормализованному email (с хешем пароля)."""
        result = await self._session.execute(
            select(User).where(User.email == email)
        )
        user = result.scalar_one_or_none()
        if user is None:
            return None
        return AuthUser(profile=_to_profile(user), password_hash=user.password_hash)

    async def create(
        self,
        *,
        telegram_id: int,
        email: str | None = None,
        password_hash: str | None = None,
        first_name: str | None = None,
        username: str | None = None,
        role: str = "buyer",
        subscription_tier: str = "free",
    ) -> UserProfile:
        """Создать пользователя и вернуть профиль.

        Роль и тариф задаются явно: `server_default` появляются только после
        `refresh`, а ответ (`UserOut`) валидирует их как обязательные.

        Raises:
            EmailConflict: Email уже занят (гонка с параллельной регистрацией).
            DuplicateUserId: Синтетический id занят — вызывающий повторит.
        """
        user = User(
            telegram_id=telegram_id,
            username=username,
            first_name=first_name,
            email=email,
            password_hash=password_hash,
            role=role,
            subscription_tier=subscription_tier,
            is_active=True,
        )
        self._session.add(user)
        try:
            await self._session.commit()
        except IntegrityError:
            await self._session.rollback()
            # Различаем коллизию email (409) и telegram_id (retry).
            if email is not None and await self._email_taken(email):
                raise EmailConflict from None
            raise DuplicateUserId from None
        await self._session.refresh(user)
        return _to_profile(user)

    async def update_profile(
        self,
        telegram_id: int,
        *,
        username: str | None = None,
        first_name: str | None = None,
        last_name: str | None = None,
        language_code: str | None = None,
    ) -> UserProfile | None:
        """Обновить профильные поля; `None`, если пользователя нет."""
        user = await self._session.get(User, telegram_id)
        if user is None:
            return None
        self._apply_profile(
            user,
            username=username,
            first_name=first_name,
            last_name=last_name,
            language_code=language_code,
        )
        await self._session.commit()
        await self._session.refresh(user)
        return _to_profile(user)

    async def upsert(
        self,
        *,
        telegram_id: int,
        username: str | None = None,
        first_name: str | None = None,
        last_name: str | None = None,
        language_code: str | None = None,
    ) -> UpsertOutcome:
        """Найти или создать пользователя и обновить профиль из Telegram.

        `is_active` не трогается: забаненный пользователь не «разбанивается»
        повторным входом.
        """
        user = await self._session.get(User, telegram_id)
        created = user is None
        if user is None:
            user = User(
                telegram_id=telegram_id,
                role="buyer",
                subscription_tier="free",
                is_active=True,
            )
            self._session.add(user)
        self._apply_profile(
            user,
            username=username,
            first_name=first_name,
            last_name=last_name,
            language_code=language_code,
        )
        await self._session.commit()
        await self._session.refresh(user)
        return UpsertOutcome(profile=_to_profile(user), created=created)

    async def _email_taken(self, email: str) -> bool:
        """Есть ли уже аккаунт с этим email (после rollback)."""
        result = await self._session.execute(
            select(User).where(User.email == email)
        )
        return result.scalar_one_or_none() is not None

    @staticmethod
    def _apply_profile(
        user: User,
        *,
        username: str | None,
        first_name: str | None,
        last_name: str | None,
        language_code: str | None,
    ) -> None:
        """Перенести профильные поля из Telegram в ORM-объект."""
        user.username = username
        user.first_name = first_name
        user.last_name = last_name
        if language_code:
            user.language_code = language_code
