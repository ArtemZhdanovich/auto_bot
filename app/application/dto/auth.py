"""DTO авторизации: что сценарии отдают транспорту (без ORM, Redis и HTTP)."""

from __future__ import annotations

from dataclasses import dataclass

from app.application.dto.users import UserProfile

# Статусы одноразового login-кода (хранятся в Redis как есть).
LOGIN_PENDING = "pending"
LOGIN_OK = "ok"
LOGIN_EXPIRED = "expired"


@dataclass(frozen=True, slots=True)
class TokenPair:
    """Пара JWT: короткий access + длинный refresh."""

    access_token: str
    refresh_token: str


@dataclass(frozen=True, slots=True)
class AuthResult:
    """Успешная аутентификация: токены и публичный профиль."""

    tokens: TokenPair
    user: UserProfile


@dataclass(frozen=True, slots=True)
class AuthUser:
    """Аккаунт для проверки пароля: профиль + хеш.

    `password_hash` не покидает сценарий: в ответ он не попадает.
    """

    profile: UserProfile
    password_hash: str | None = None


@dataclass(frozen=True, slots=True)
class TelegramIdentity:
    """Профиль из проверенного initData."""

    telegram_id: int
    username: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    language_code: str | None = None


@dataclass(frozen=True, slots=True)
class LoginLink:
    """Созданный код входа: `state` уходит только в HttpOnly-cookie."""

    code: str
    state: str
    expires_in: int


@dataclass(frozen=True, slots=True)
class LoginCode:
    """Содержимое одноразового кода входа (Redis → DTO)."""

    status: str
    state_hash: str = ""
    initiator_ip: str | None = None
    initiator_ua: str | None = None
    created_at: int | None = None
    telegram_id: int | None = None
    username: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    language_code: str | None = None


@dataclass(frozen=True, slots=True)
class LoginContext:
    """Контекст инициатора для бота (UX-фикс session fixation)."""

    code: str
    status: str
    initiator_ip: str | None = None
    initiator_ua: str | None = None
    created_at: int | None = None


@dataclass(frozen=True, slots=True)
class LoginPollResult:
    """Результат поллинга: статус и, если код подтверждён, токены."""

    status: str
    auth: AuthResult | None = None
