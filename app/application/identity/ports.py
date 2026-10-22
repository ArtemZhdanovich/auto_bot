"""Порты контура идентичности: хранилища и внешние системы.

Интерфейсы объявлены здесь (прикладной слой), реализации — в
`infrastructure/` (SQLAlchemy, Redis, JWT, PBKDF2, Telegram).
"""

from __future__ import annotations

from typing import Protocol

from app.application.identity.dto import (
    AuthUser,
    LoginCode,
    OnboardingResult,
    TelegramIdentity,
    TokenPair,
    UpsertOutcome,
    UserProfile,
)


class UserRepository(Protocol):
    """Поиск, создание и обновление пользователя.

    Реализация коммитит и обновляет объект сама: наружу отдаётся DTO профиля,
    а не «грязный» ORM-объект.
    """

    async def find(self, telegram_id: int) -> UserProfile | None:
        """Найти профиль по `telegram_id`."""

    async def find_by_email(self, email: str) -> AuthUser | None:
        """Найти аккаунт по нормализованному email (с хешем пароля)."""

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
        """Создать пользователя.

        Raises:
            EmailConflict: email уже занят.
            DuplicateUserId: синтетический id занят, нужна новая попытка.
        """

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

    async def upsert(
        self,
        *,
        telegram_id: int,
        username: str | None = None,
        first_name: str | None = None,
        last_name: str | None = None,
        language_code: str | None = None,
    ) -> UpsertOutcome:
        """Найти или создать пользователя и обновить профиль из Telegram."""


class UserUpsertRepository(Protocol):
    """Upsert профиля из Telegram.

    Реализация коммитит и обновляет объект сама: вызывающий получает готовый
    профиль с серверными дефолтами (`role`, `created_at`), а не «грязный»
    ORM-объект.
    """

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


class ProfileRepository(Protocol):
    """Хранилище профиля: только публичная проекция, без ORM."""

    async def get(self, user_id: int) -> UserProfile | None:
        """Профиль пользователя или `None`, если его нет."""

    async def mark_onboarded(self, user_id: int) -> OnboardingResult | None:
        """Идемпотентно проставить `onboarded_at`.

        `None` — пользователя нет; `first_time=False` — флаг уже стоял.
        """


class LoginCodeStore(Protocol):
    """Одноразовые коды входа в Redis (TTL, привязка к браузеру)."""

    async def save_pending(
        self,
        code: str,
        *,
        state_hash: str,
        initiator_ip: str | None,
        initiator_ua: str,
        created_at: int,
        ttl_sec: int,
    ) -> None:
        """Сохранить новый код в статусе `pending`."""

    async def load(self, code: str) -> LoginCode | None:
        """Прочитать запись; `None` — кода нет или он повреждён."""

    async def touch(self, code: str, ttl_sec: int) -> None:
        """Продлить TTL (sliding window, пока пользователь в боте)."""

    async def claim(self, code: str) -> LoginCode | None:
        """Атомарно забрать запись: второй вызов вернёт `None`."""

    async def confirm(
        self,
        code: str,
        *,
        telegram_id: int,
        username: str | None,
        first_name: str | None,
        last_name: str | None,
        language_code: str | None,
        ttl_sec: int,
    ) -> LoginCode | None:
        """Пометить код подтверждённым; вернуть прежнюю запись или `None`."""

    async def delete(self, code: str) -> bool:
        """Удалить код; `False` — его уже нет."""


class TokenIssuer(Protocol):
    """Выпуск JWT и разбор refresh-токена (с проверкой `type`)."""

    def issue(self, user_id: int) -> TokenPair:
        """Выпустить access + refresh для пользователя."""

    def refresh_subject(self, token: str) -> int:
        """Вернуть `sub` refresh-токена.

        Raises:
            InvalidToken: подпись/срок/тип не в порядке.
        """


class AccessTokenVerifier(Protocol):
    """Проверка access-JWT из заголовка `Authorization` (транспорт auth)."""

    def verify(self, token: str) -> int:
        """Вернуть `telegram_id` из валидного access-токена.

        Raises:
            InvalidToken: подпись/срок/тип (`type == "access"`) не в порядке.
        """


class InitDataVerifier(Protocol):
    """HMAC-проверка `Telegram.WebApp.initData`."""

    def verify(self, init_data: str) -> TelegramIdentity:
        """Проверить подпись и срок, вернуть профиль.

        Raises:
            InvalidInitData: подпись/срок/структура не в порядке.
            BotNotConfigured: не задан `BOT_TOKEN`.
        """


class PasswordHasher(Protocol):
    """PBKDF2: хеширование и проверка пароля (blocking — внутри адаптера)."""

    async def hash(self, password: str) -> str:
        """Посчитать хеш для нового пароля."""

    async def verify(self, password: str, stored_hash: str | None) -> bool:
        """Сверить пароль; `None` — пользователя нет (тайминг выравнивается)."""
