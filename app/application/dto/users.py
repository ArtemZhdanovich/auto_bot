"""DTO пользователя: профиль, который сценарий отдаёт наружу (без ORM)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


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
