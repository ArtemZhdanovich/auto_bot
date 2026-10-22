"""Публикация: FastStream-адаптер и REQUEST-скоупный буфер событий."""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID, uuid4

import pytest
from faststream.rabbit import RabbitBroker, RabbitMessage, TestRabbitBroker

from app.domain.shared.events import DomainEvent
from app.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.infrastructure.messaging.publisher import (
    BufferedEventPublisher,
    FastStreamRawPublisher,
)
from app.infrastructure.messaging.topology import (
    FETCH_QUEUE,
    SEND_QUEUE,
    TASKS_EXCHANGE,
)


class RecordingRawPublisher:
    """Фейковый `RawEventPublisher`: запоминает вызовы publish_raw."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any], str]] = []

    async def publish_raw(
        self, routing_key: str, payload: dict[str, Any], event_id: UUID | str
    ) -> None:
        self.calls.append((routing_key, payload, str(event_id)))


@pytest.mark.asyncio
async def test_publish_raw_reaches_fetch_queue_subscriber() -> None:
    """Сообщение доходит до подписчика FETCH_QUEUE, event_id — в заголовке."""
    broker = RabbitBroker()
    received: list[RabbitMessage] = []

    @broker.subscriber(queue=FETCH_QUEUE, exchange=TASKS_EXCHANGE)
    async def handle(message: RabbitMessage) -> None:
        received.append(message)

    event_id = uuid4()
    async with TestRabbitBroker(broker) as test_broker:
        await FastStreamRawPublisher(test_broker).publish_raw(
            "fetch_source_task", {"source": "encar", "max_pages": 3}, event_id
        )

    assert len(received) == 1
    assert json.loads(received[0].body) == {"source": "encar", "max_pages": 3}
    assert received[0].headers["event_id"] == str(event_id)


@pytest.mark.asyncio
async def test_publish_raw_routes_only_to_matching_queue() -> None:
    """routing_key уходит в publish как есть: чужой ключ не доходит."""
    broker = RabbitBroker()
    fetched: list[RabbitMessage] = []
    sent: list[RabbitMessage] = []

    @broker.subscriber(queue=FETCH_QUEUE, exchange=TASKS_EXCHANGE)
    async def handle_fetch(message: RabbitMessage) -> None:
        fetched.append(message)

    @broker.subscriber(queue=SEND_QUEUE, exchange=TASKS_EXCHANGE)
    async def handle_send(message: RabbitMessage) -> None:
        sent.append(message)

    async with TestRabbitBroker(broker) as test_broker:
        await FastStreamRawPublisher(test_broker).publish_raw(
            "fetch_source_task", {"source": "encar"}, uuid4()
        )

    assert len(fetched) == 1
    assert sent == []


@pytest.mark.asyncio
async def test_buffered_publisher_holds_events_until_flush() -> None:
    raw = RecordingRawPublisher()
    buffered = BufferedEventPublisher(raw)
    event = DomainEvent()

    await buffered.publish(event)
    assert len(buffered) == 1
    assert raw.calls == []

    await buffered.flush()
    assert len(buffered) == 0
    assert raw.calls == [("domain.event", {}, str(event.event_id))]


@pytest.mark.asyncio
async def test_buffered_publisher_uses_explicit_routing_key() -> None:
    raw = RecordingRawPublisher()
    buffered = BufferedEventPublisher(raw)
    event = DomainEvent()

    await buffered.publish(event, "lead.created")
    await buffered.flush()

    assert raw.calls == [("lead.created", {}, str(event.event_id))]


@pytest.mark.asyncio
async def test_buffered_publisher_discard_drops_buffer() -> None:
    raw = RecordingRawPublisher()
    buffered = BufferedEventPublisher(raw)
    await buffered.publish(DomainEvent())

    buffered.discard()
    await buffered.flush()

    assert len(buffered) == 0
    assert raw.calls == []


@pytest.mark.asyncio
async def test_uow_flushes_buffer_only_after_commit(fake_session) -> None:
    """Порт `EventPublisher`: публикация идёт после успешного commit."""
    raw = RecordingRawPublisher()
    committed_at_publish: list[bool] = []
    publish_raw = raw.publish_raw

    async def spy(
        routing_key: str, payload: dict[str, Any], event_id: UUID | str
    ) -> None:
        committed_at_publish.append(fake_session.committed)
        await publish_raw(routing_key, payload, event_id)

    raw.publish_raw = spy  # type: ignore[method-assign]

    uow = SqlAlchemyUnitOfWork(fake_session, publisher=BufferedEventPublisher(raw))
    async with uow:
        uow.collect(DomainEvent())
        await uow.commit()

    assert committed_at_publish == [True]
    assert len(raw.calls) == 1
