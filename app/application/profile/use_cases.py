"""Сценарии профиля: чтение `/me` и идемпотентный онбординг.

`/me` отдаёт `UserProfile` — проекцию без секретов. Онбординг идемпотентен
на уровне хранилища (`UPDATE ... WHERE onboarded_at IS NULL`), поэтому
повторный вызов не перезаписывает время первого прохождения.
"""

from __future__ import annotations

import logging
from typing import Protocol

from app.application.dto.profile import OnboardingResult, UserProfile
from app.application.errors import NotFound

logger = logging.getLogger(__name__)


class ProfileRepository(Protocol):
    """Хранилище профиля: только публичная проекция, без ORM."""

    async def get(self, user_id: int) -> UserProfile | None:
        """Профиль пользователя или `None`, если его нет."""

    async def mark_onboarded(self, user_id: int) -> OnboardingResult | None:
        """Идемпотентно проставить `onboarded_at`.

        `None` — пользователя нет; `first_time=False` — флаг уже стоял.
        """


class GetProfile:
    """Профиль текущего пользователя для `GET /api/me`."""

    def __init__(self, profiles: ProfileRepository) -> None:
        self._profiles = profiles

    async def execute(self, user_id: int) -> UserProfile:
        """Вернуть публичный профиль.

        Raises:
            NotFound: Пользователя нет (токен на удалённую запись).
        """
        profile = await self._profiles.get(user_id)
        if profile is None:
            raise NotFound("User not found")
        return profile


class CompleteOnboarding:
    """Отметить онбординг пройденным (идемпотентно)."""

    def __init__(self, profiles: ProfileRepository) -> None:
        self._profiles = profiles

    async def execute(self, user_id: int) -> OnboardingResult:
        """Проставить `onboarded_at` и вернуть профиль.

        Raises:
            NotFound: Пользователя нет.
        """
        result = await self._profiles.mark_onboarded(user_id)
        if result is None:
            raise NotFound("User not found")
        if result.first_time and result.profile.onboarded_at is not None:
            logger.info(
                "user_onboarded user_id=%s onboarded_at=%s",
                user_id, result.profile.onboarded_at.isoformat(),
            )
        return result
