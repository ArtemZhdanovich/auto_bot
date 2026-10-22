"""Публикация событий в RabbitMQ.

`RawEventPublisher` — порт для relay: готовый routing key и сырой payload.
`FastStreamRawPublisher` — адаптер над `RabbitBroker.publish` в exchange `tasks`.
`BufferedEventPublisher` — REQUEST-скоуп: доменные события копятся до
`flush()`, который UoW вызывает строго после commit.
"""

from __future__ import annotations

from typing import Any, Protocol
from uuid import UUID

from faststream.rabbit import RabbitBroker

from app.domain.shared.events import DomainEvent
from app.infrastructure.db.mappers import event_to_dict, routing_key_for
from app.infrastructure.messaging.topology import TASKS_EXCHANGE


class RawEventPublisher(Protocol):
    """Публикация уже сериализованного события."""

    async def publish_raw(
        self, routing_key: str, payload: dict[str, Any], event_id: UUID | str
    ) -> None: ...


class FastStreamRawPublisher:
    """Отправляет событие в exchange `tasks` с `event_id` в заголовке."""

    def __init__(self, broker: RabbitBroker) -> None:
        self._broker = broker

    async def publish_raw(
        self, routing_key: str, payload: dict[str, Any], event_id: UUID | str
    ) -> None:
        await self._broker.publish(
            payload,
            exchange=TASKS_EXCHANGE,
            routing_key=routing_key,
            headers={"event_id": str(event_id)},
            persist=True,
        )


class BufferedEventPublisher:
    """Копит события запроса и публикует их после commit.

    Реализует порт `EventPublisher`: `publish` не делает I/O, `flush`
    отправляет накопленное, `discard` забывает буфер при rollback.

    `flush` отправляет по одному и удаляет отправленное из буфера, поэтому
    после сбоя неотправленное (включая упавшее событие) остаётся в буфере.
    """

    def __init__(self, publisher: RawEventPublisher) -> None:
        self._publisher = publisher
        self._pending: list[tuple[DomainEvent, str]] = []

    def __len__(self) -> int:
        return len(self._pending)

    async def publish(
        self, event: DomainEvent, routing_key: str | None = None
    ) -> None:
        self._pending.append((event, routing_key or routing_key_for(event)))

    async def flush(self) -> None:
        """Отправить буфер по порядку; вызывается после успешного commit."""
        while self._pending:
            event, routing_key = self._pending[0]
            envelope = event_to_dict(event)
            await self._publisher.publish_raw(
                routing_key, envelope["payload"], event.event_id
            )
            self._pending.pop(0)

    def discard(self) -> None:
        """Забыть буфер, ничего не публикуя (rollback транзакции)."""
        self._pending.clear()
