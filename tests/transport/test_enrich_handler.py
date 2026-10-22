"""Консьюмер очереди `enrich`: подписка, идемпотентность и логика обработчика.

Живой RabbitMQ не нужен: маршрутизацию даёт `TestRabbitBroker`, зависимости
обработчика — настоящий Dishka-контейнер с подменёнными сессией и гардой
(тот же `setup_dishka`, что и в `app/main_consumer.py`).
"""

from __future__ import annotations

from typing import Any

import pytest
from dishka import Provider, Scope, provide
from dishka_faststream import setup_dishka
from faststream.rabbit import RabbitBroker, TestRabbitBroker
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.shared.ports import IdempotencyGuard
from app.infrastructure.db.models import Listing
from app.di.container import make_container
from app.infrastructure.messaging.idempotency import RedisIdempotencyGuard
from app.infrastructure.messaging.topology import ENRICH_QUEUE, TASKS_EXCHANGE
from app.main_consumer import TransportProvider
from app.transport.messaging.contracts import EnrichListingCommand
from app.transport.messaging.handlers import enrich

ROUTING_KEY = "enrich_listing"
KEY = "idem-key-0001"


class FakeGuard:
    """Гарда, которая отдаёт заранее заданный ответ и запоминает вызовы."""

    def __init__(self, allow: bool = True) -> None:
        self.allow = allow
        self.acquired: list[tuple[str, int]] = []
        self.released: list[str] = []

    async def acquire(self, key: str, ttl_sec: int) -> bool:
        self.acquired.append((key, ttl_sec))
        return self.allow

    async def release(self, key: str) -> None:
        self.released.append(key)


class FakeSession:
    """Сессия с одним лотом: `get` ищет его по PK, `commit` считает вызовы."""

    def __init__(self, listing: Listing | None = None) -> None:
        self.listing = listing
        self.get_calls: list[tuple[Any, Any]] = []

    async def get(self, model: Any, pk: Any) -> Any:
        self.get_calls.append((model, pk))
        if self.listing is not None and self.listing.id == pk:
            return self.listing
        return None

    async def commit(self) -> None:
        return None


def _provider(session: FakeSession, guard: FakeGuard) -> Provider:
    """Провайдер-перекрытие: сессия и гарда вместо настоящих."""

    class TestProvider(Provider):
        @provide(scope=Scope.REQUEST)
        def session(self) -> AsyncSession:
            return session  # type: ignore[return-value]

        @provide(scope=Scope.APP)
        def guard(self) -> IdempotencyGuard:
            return guard

    return TestProvider()


def _listing(listing_id: int = 7) -> Listing:
    """Лот encar без спеков: `enrich_listing_specs` было бы что догружать."""
    return Listing(
        id=listing_id,
        source="encar",
        source_id="39281726",
        url="https://fem.encar.com/cars/detail/39281726",
    )


async def _deliver(
    payload: dict[str, Any], session: FakeSession, guard: FakeGuard
) -> None:
    """Опубликовать команду в `tasks` и дождаться обработки в памяти."""
    broker = RabbitBroker()
    enrich.register(broker)
    container = make_container(_provider(session, guard))
    setup_dishka(container, broker=broker, auto_inject=True)
    try:
        async with TestRabbitBroker(broker) as test_broker:
            await test_broker.publish(
                payload, exchange=TASKS_EXCHANGE, routing_key=ROUTING_KEY
            )
    finally:
        await container.close()


def test_register_binds_enrich_queue_to_tasks_exchange() -> None:
    """Подписка создаётся на quorum-очередь `enrich` в exchange `tasks`."""
    broker = RabbitBroker()

    enrich.register(broker)

    assert len(broker.subscribers) == 1
    subscriber = broker.subscribers[0]
    assert subscriber.queue.name == ENRICH_QUEUE.name == "enrich"
    assert subscriber.queue.routing_key == ROUTING_KEY
    assert subscriber.exchange.name == TASKS_EXCHANGE.name == "tasks"


def test_command_forbids_extra_fields() -> None:
    """Лишнее поле в сообщении — ошибка контракта, а не тихий пропуск."""
    with pytest.raises(ValidationError):
        EnrichListingCommand.model_validate(
            {"listing_id": 1, "idempotency_key": KEY, "unexpected": True}
        )


def test_command_requires_positive_id_and_long_key() -> None:
    with pytest.raises(ValidationError):
        EnrichListingCommand(listing_id=0, idempotency_key=KEY)
    with pytest.raises(ValidationError):
        EnrichListingCommand(listing_id=1, idempotency_key="short")


async def test_payload_with_extra_field_never_reaches_handler() -> None:
    """Контракт проверяется на границе: обработчик и гарда не вызываются."""
    session = FakeSession(_listing())
    guard = FakeGuard()

    with pytest.raises(ValidationError):
        await _deliver(
            {"listing_id": 7, "idempotency_key": KEY, "unexpected": True},
            session,
            guard,
        )

    assert guard.acquired == []
    assert session.get_calls == []


async def test_handler_enriches_listing_and_holds_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Первая доставка: ключ занят на 7 дней, лот обогащён, ключ не снят."""
    calls: list[tuple[Any, Any]] = []

    async def fake_enrich(session: Any, listing: Any) -> bool:
        calls.append((session, listing))
        return True

    monkeypatch.setattr(enrich, "enrich_listing_specs", fake_enrich)
    session = FakeSession(_listing())
    guard = FakeGuard()

    await _deliver({"listing_id": 7, "idempotency_key": KEY}, session, guard)

    assert guard.acquired == [(f"idem:enrich:{KEY}", 7 * 24 * 60 * 60)]
    assert session.get_calls == [(Listing, 7)]
    assert calls == [(session, session.listing)]
    assert guard.released == []


async def test_duplicate_delivery_skips_work(monkeypatch: pytest.MonkeyPatch) -> None:
    """Повторная доставка: ключ занят, БД и detail-источник не трогаем."""
    calls: list[Any] = []

    async def fake_enrich(session: Any, listing: Any) -> bool:
        calls.append(listing)
        return True

    monkeypatch.setattr(enrich, "enrich_listing_specs", fake_enrich)
    session = FakeSession(_listing())
    guard = FakeGuard(allow=False)

    await _deliver({"listing_id": 7, "idempotency_key": KEY}, session, guard)

    assert guard.acquired == [(f"idem:enrich:{KEY}", 7 * 24 * 60 * 60)]
    assert session.get_calls == []
    assert calls == []


async def test_missing_listing_is_skipped_without_release(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Лота нет: обогащение не вызывается, ключ остаётся занятым."""
    calls: list[Any] = []

    async def fake_enrich(session: Any, listing: Any) -> bool:
        calls.append(listing)
        return True

    monkeypatch.setattr(enrich, "enrich_listing_specs", fake_enrich)
    session = FakeSession(None)
    guard = FakeGuard()

    await _deliver({"listing_id": 404, "idempotency_key": KEY}, session, guard)

    assert session.get_calls == [(Listing, 404)]
    assert calls == []
    assert guard.released == []


async def test_failure_releases_key_and_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ошибка обогащения: ключ снят (нужен повтор), исключение не проглочено."""
    error = RuntimeError("detail unavailable")

    async def failing_enrich(session: Any, listing: Any) -> bool:
        raise error

    monkeypatch.setattr(enrich, "enrich_listing_specs", failing_enrich)
    session = FakeSession(_listing())
    guard = FakeGuard()

    with pytest.raises(RuntimeError, match="detail unavailable"):
        await _deliver({"listing_id": 7, "idempotency_key": KEY}, session, guard)

    assert guard.released == [f"idem:enrich:{KEY}"]


async def test_entrypoint_provides_redis_idempotency_guard() -> None:
    """Провайдер entrypoint-а закрывает `FromDishka[IdempotencyGuard]` без сети."""
    container = make_container(TransportProvider())
    try:
        guard = await container.get(IdempotencyGuard)
    finally:
        await container.close()

    assert isinstance(guard, RedisIdempotencyGuard)


async def test_redis_guard_sets_key_with_ttl_and_releases_it(fake_redis: Any) -> None:
    """`SET NX EX`: первый захват — True, повтор — False, release снимает ключ."""
    guard = RedisIdempotencyGuard(fake_redis)

    assert await guard.acquire("idem:enrich:k1", ttl_sec=60) is True
    assert fake_redis.ttls["idem:enrich:k1"] == 60
    assert await guard.acquire("idem:enrich:k1", ttl_sec=60) is False

    await guard.release("idem:enrich:k1")
    assert await guard.acquire("idem:enrich:k1", ttl_sec=60) is True
