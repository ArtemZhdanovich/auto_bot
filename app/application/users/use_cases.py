"""Сценарий upsert пользователя из Telegram."""

from __future__ import annotations

from app.application.analytics.use_cases import TrackEvent
from app.application.identity.dto import UpsertUserCommand, UserProfile
from app.application.identity.ports import UserUpsertRepository
from app.domain.analytics.events import EVENT_USER_REGISTERED


class UpsertUser:
    """Создать/обновить пользователя и отметить регистрацию в воронке."""

    def __init__(self, users: UserUpsertRepository, track: TrackEvent) -> None:
        self._users = users
        self._track = track

    async def execute(self, command: UpsertUserCommand) -> UserProfile:
        """Вернуть профиль; событие — только при первой регистрации.

        Args:
            command: Профиль из Telegram.

        Returns:
            Профиль с серверными дефолтами.
        """
        outcome = await self._users.upsert(
            telegram_id=command.telegram_id,
            username=command.username,
            first_name=command.first_name,
            last_name=command.last_name,
            language_code=command.language_code,
        )
        if outcome.created:  # воронка: событие только при первой регистрации
            await self._track.execute(
                command.telegram_id,
                EVENT_USER_REGISTERED,
                {"source": command.source},
            )
        return outcome.profile
