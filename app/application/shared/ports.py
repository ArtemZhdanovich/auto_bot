"""Порты прикладного слоя: границы транзакций и внешние эффекты."""

from __future__ import annotations

from types import TracebackType
from typing import Any, Protocol, Self

from app.domain.shared.events import DomainEvent


class UnitOfWork(Protocol):
    """Одна транзакция на HTTP-запрос или сообщение.

    Использование::

        async with uow:
            ...
            await uow.commit()
    """

    async def __aenter__(self) -> Self: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...

    async def commit(self) -> None:
        """Зафиксировать изменения (и события) одной транзакцией."""

    async def rollback(self) -> None:
        """Отменить транзакцию."""


class EventTracker(Protocol):
    """Запись события воронки.

    Реализация обязана глотать сбои: аналитика не ломает бизнес-операцию
    (см. `app.application.analytics.use_cases.TrackEvent`).
    """

    async def __call__(
        self,
        user_id: int | None,
        name: str,
        props: dict[str, Any] | None = None,
    ) -> None: ...


class AuditLog(Protocol):
    """Журнал admin-действий (security-события).

    Секреты (raw API-ключи) в `payload` не передаются — только префиксы.
    """

    async def __call__(
        self,
        *,
        action: str,
        entity: str,
        entity_id: str,
        payload: dict[str, Any],
    ) -> None: ...


class EventPublisher(Protocol):
    """Публикация доменных событий.

    `publish` может буферизовать; `flush` отправляет буфер. Вызывается
    после успешного `commit`, чтобы не публиковать откатённое.
    """

    async def publish(self, event: DomainEvent, routing_key: str) -> None: ...

    async def flush(self) -> None: ...


class IdempotencyGuard(Protocol):
    """Защита обработчика от повторной доставки сообщения."""

    async def acquire(self, key: str, ttl_sec: int) -> bool: ...

    async def release(self, key: str) -> None: ...


# ---------------------------------------------------------------------------
# Кросс-сервисные порты внешних систем (нужны 2+ сервисам)
# ---------------------------------------------------------------------------


class CachePort(Protocol):
    """Key-value кэш (Redis)."""

    async def get(self, key: str) -> bytes | str | None: ...

    async def get_many(self, keys: list[str]) -> list[bytes | str | None]: ...

    async def set(self, key: str, value: str, ttl_sec: int | None = None) -> None: ...

    async def set_if_absent(self, key: str, value: str, ttl_sec: int) -> bool: ...

    async def delete(self, key: str) -> None: ...

    async def incr(self, key: str) -> int: ...

    async def expire(self, key: str, ttl_sec: int) -> None: ...


class LLMPort(Protocol):
    """Один вызов языковой модели."""

    async def complete(
        self, messages: list[dict], tools: list[dict] | None = None
    ) -> object: ...


class ClockPort(Protocol):
    """Источник времени (детерминированные тесты)."""

    def now(self) -> object: ...

