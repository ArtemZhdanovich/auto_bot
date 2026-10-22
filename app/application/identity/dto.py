"""DTO контура идентичности: профиль, аутентификация, onboarding.

Сценарии отдают наружу ровно эти структуры — без ORM, Redis и HTTP.
Секреты (`password_hash`, email, токены) в публичные DTO не попадают
by design: утечка возможна только осознанной правкой этого файла.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


# ---------------------------------------------------------------------------
# Пользователь
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class UserProfile:
    """Профиль пользователя в объёме `UserOut`."""

    telegram_id: int
    username: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    language_code: str | None = None
    role: str | None = None
    subscription_tier: str | None = None
    subscription_until: datetime | None = None
    created_at: datetime | None = None
    is_active: bool = True
    onboarded_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class CurrentUser:
    """Аутентифицированный пользователь для роутеров: минимум нужного.

    Роутерам нужен `telegram_id` (сценарии принимают id), `role`
    (транспортная авторизация, напр. «Admin only») и снепшот подписки
    (`tier_config` в сценариях фильтров читает её с юзера команды).
    Профиль целиком отдаёт `GET /api/me` через `GetProfile`.
    """

    telegram_id: int
    role: str | None = None
    subscription_tier: str | None = None
    subscription_until: datetime | None = None


@dataclass(frozen=True, slots=True)
class UpsertOutcome:
    """Итог upsert: профиль и признак первой регистрации."""

    profile: UserProfile
    created: bool


@dataclass(frozen=True, slots=True)
class UpsertUserCommand:
    """Вход сценария upsert: профильные поля из Telegram и канал."""

    telegram_id: int
    username: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    language_code: str | None = None
    source: str | None = None


@dataclass(frozen=True, slots=True)
class OnboardingResult:
    """Итог `POST /api/me/onboarded`.

    `first_time` — вызов действительно проставил `onboarded_at` (а не был
    повтором): по нему пишется лог, первое значение не перезаписывается.
    """

    profile: UserProfile
    first_time: bool


# ---------------------------------------------------------------------------
# Аутентификация
# ---------------------------------------------------------------------------

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


@dataclass(frozen=True, slots=True)
class RegisterCommand:
    """Регистрация с сайта."""

    email: str
    password: str
    first_name: str | None = None


@dataclass(frozen=True, slots=True)
class LoginCommand:
    """Вход по email и паролю."""

    email: str
    password: str


@dataclass(frozen=True, slots=True)
class ConfirmLoginCommand:
    """Подтверждение кода ботом (после явного «✅ Это я»)."""

    code: str
    telegram_id: int
    username: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    language_code: str | None = None
    acknowledged_at: int | None = None
