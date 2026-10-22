"""Relay outbox: публикация после commit, ретраи и живучесть цикла."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.infrastructure.db.models import Base
from app.infrastructure.db.outbox import OutboxMessage
from app.infrastructure.messaging.relay import OutboxRelay

MARKER = "RelayTest"  # event_type тестовых строк: чужие строки не трогаем


class FakeRawPublisher:
    """Фейковый `RawEventPublisher` с возможностью падать."""

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[tuple[str, dict[str, Any], str]] = []

    async def publish_raw(
        self, routing_key: str, payload: dict[str, Any], event_id: UUID | str
    ) -> None:
        if self.fail:
            raise RuntimeError("broker is down")
        self.calls.append((routing_key, payload, str(event_id)))


@pytest.fixture
async def relay_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Собственный engine на тестовой БД (схема outbox создаётся, если её нет)."""
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not set")

    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(
                Base.metadata.create_all, tables=[OutboxMessage.__table__]
            )
            await conn.execute(
                delete(OutboxMessage).where(OutboxMessage.event_type == MARKER)
            )
    except Exception as exc:  # noqa: BLE001 — окружение без БД
        await engine.dispose()
        pytest.skip(f"test database unavailable: {exc}")

    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


async def _add_messages(
    factory: async_sessionmaker[AsyncSession], count: int
) -> list[UUID]:
    event_ids = [uuid4() for _ in range(count)]
    async with factory() as session:
        for index, event_id in enumerate(event_ids):
            session.add(
                OutboxMessage(
                    event_id=event_id,
                    event_type=MARKER,
                    routing_key="lead.created",
                    payload={"index": index},
                    occurred_at=datetime.now(UTC),
                )
            )
        await session.commit()
    return event_ids


async def _load(factory: async_sessionmaker[AsyncSession]) -> list[OutboxMessage]:
    async with factory() as session:
        result = await session.execute(
            select(OutboxMessage)
            .where(OutboxMessage.event_type == MARKER)
            .order_by(OutboxMessage.id)
        )
        return list(result.scalars().all())


@pytest.mark.asyncio
async def test_drain_once_publishes_each_row_once(relay_factory) -> None:
    event_ids = await _add_messages(relay_factory, 2)
    publisher = FakeRawPublisher()
    relay = OutboxRelay(relay_factory, publisher)

    assert await relay.drain_once() == 2
    assert [call[2] for call in publisher.calls] == [str(i) for i in event_ids]
    assert publisher.calls[0][:2] == ("lead.created", {"index": 0})

    # Повторный проход не публикует уже отправленное.
    assert await relay.drain_once() == 0
    assert len(publisher.calls) == 2

    rows = await _load(relay_factory)
    assert all(row.published_at is not None for row in rows)
    assert all(row.attempts == 0 for row in rows)


@pytest.mark.asyncio
async def test_publish_failure_keeps_row_and_records_attempt(relay_factory) -> None:
    [event_id] = await _add_messages(relay_factory, 1)
    publisher = FakeRawPublisher(fail=True)
    relay = OutboxRelay(relay_factory, publisher)

    assert await relay.drain_once() == 0

    [row] = await _load(relay_factory)
    assert row.published_at is None
    assert row.attempts == 1
    assert row.last_error is not None
    assert "broker is down" in row.last_error

    # Брокер поднялся — следующий проход публикует ту же строку.
    publisher.fail = False
    assert await relay.drain_once() == 1

    [row] = await _load(relay_factory)
    assert row.published_at is not None
    assert row.attempts == 1
    assert publisher.calls == [("lead.created", {"index": 0}, str(event_id))]


@pytest.mark.asyncio
async def test_drain_once_respects_batch(relay_factory) -> None:
    await _add_messages(relay_factory, 3)
    publisher = FakeRawPublisher()
    relay = OutboxRelay(relay_factory, publisher, batch=2)

    assert await relay.drain_once() == 2
    assert await relay.drain_once() == 1
    assert await relay.drain_once() == 0


@pytest.mark.asyncio
async def test_run_survives_publish_error_and_stops(relay_factory) -> None:
    await _add_messages(relay_factory, 1)
    publisher = FakeRawPublisher(fail=True)
    relay = OutboxRelay(relay_factory, publisher, interval=0.01)
    stop = asyncio.Event()

    task = asyncio.create_task(relay.run(stop))
    await asyncio.sleep(0.05)
    stop.set()
    await asyncio.wait_for(task, timeout=1)

    # Ошибка публикации не завершила цикл: попыток накопилось больше одной.
    [row] = await _load(relay_factory)
    assert row.attempts > 1
    assert row.published_at is None
