"""Порты диалогового контура (диалог + AI-поиск + квота).

Общение с внешним миром идёт только через эти интерфейсы; реализации —
в `infrastructure/` (Redis, SQLAlchemy, LLM, очередь парсинга).
"""

from __future__ import annotations

from typing import Any, Protocol

from app.application.dialog.dto import (
    DialogReply,
    DialogSessionRecord,
    UserProfile,
)
from app.domain.dialog.state import DialogState


class UserProfilePort(Protocol):
    """Чтение пользователя для сценариев диалога."""

    async def get(self, user_id: int) -> UserProfile | None:
        """Профиль пользователя или `None`, если его нет."""


class DialogStorePort(Protocol):
    """Хранилище `DialogState` (Redis) с TTL."""

    async def load(self, user_id: int) -> DialogState | None:
        """Состояние диалога или `None` (нет ключа / повреждённый JSON)."""

    async def save(self, state: DialogState) -> None:
        """Записать состояние с TTL."""

    async def delete(self, user_id: int) -> None:
        """Удалить состояние."""


class DialogRepositoryPort(Protocol):
    """Архив диалогов и фильтры пользователя."""

    async def archive(self, state: DialogState) -> None:
        """Сохранить/обновить сессию в Postgres (в текущей транзакции)."""

    async def add_filter(self, user_id: int, criteria: dict[str, Any]) -> None:
        """Создать фильтр с уведомлениями по критериям."""

    async def commit(self) -> None:
        """Зафиксировать транзакцию."""

    async def last_session(self, user_id: int) -> DialogSessionRecord | None:
        """Последняя архивная сессия пользователя."""

    async def owned_session(
        self, session_id: str, user_id: int
    ) -> DialogSessionRecord | None:
        """Сессия только если она принадлежит `user_id` (иначе `None`)."""


class DialogAgentPort(Protocol):
    """Один ход LLM-агента."""

    async def reply(
        self, user_id: int, text: str, *, quota_limit: int | None = None
    ) -> DialogReply:
        """Ответ агента.

        Raises:
            AiUnavailable: LLM недоступна.
        """


class DialogAnalyticsPort(Protocol):
    """Событие «пользователь написал в диалог»."""

    async def dialog_message(self, user_id: int) -> None:
        """Записать событие воронки."""


class EnabledSourcesPort(Protocol):
    """Источники, включённые в парсинг."""

    async def enabled_source_names(self) -> list[str]:
        """Имена включённых источников."""


class FetchQueuePort(Protocol):
    """Постановка задачи парсинга источника."""

    async def enqueue_source(self, name: str, payload: dict[str, Any]) -> None:
        """Поставить `fetch_source_task`."""


class QuotaStore(Protocol):
    """Счётчики квоты (Redis-совместимый инкремент + TTL)."""

    async def get_int(self, key: str) -> int: ...

    async def add(self, key: str, delta: int) -> int: ...

    async def expire(self, key: str, ttl_sec: int) -> None: ...


class RAGPort(Protocol):
    """Вопрос к RAG-сервису."""

    async def ask(self, question: str, context: str | None = None) -> object: ...

    async def healthcheck(self) -> bool: ...
