"""Порт хранилища пользователей."""

from __future__ import annotations

from typing import Protocol

from app.application.dto.users import UpsertOutcome


class UserRepository(Protocol):
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
