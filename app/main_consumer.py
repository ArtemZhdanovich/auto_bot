"""Воркер-консьюмер очереди `enrich`.

Запуск:
    python -m app.main_consumer           # цикл до SIGINT/SIGTERM

Обработчики подписываются до `broker.start()`, зависимости приходят из
Dishka (`FromDishka`) на каждое сообщение: сессия БД живёт REQUEST-скоуп,
брокер и Redis — APP-скоуп. Остановка: `broker.stop()` (доигрывает
`graceful_timeout`) и `container.close()`.
"""

from __future__ import annotations

import asyncio
import logging
import signal

from dishka import AsyncContainer, Provider, Scope, provide
from dishka_faststream import setup_dishka
from faststream.rabbit import RabbitBroker
from redis.asyncio import Redis

from app.application.shared.ports import IdempotencyGuard
from app.di.container import make_container
from app.infrastructure.messaging.idempotency import RedisIdempotencyGuard
from app.infrastructure.messaging.topology import ENRICH_QUEUE
from app.transport.messaging.handlers import enrich

logger = logging.getLogger(__name__)


class TransportProvider(Provider):
    """APP: зависимости входящего транспорта.

    Гарда нет в общих провайдерах: она нужна только консьюмерам, поэтому
    регистрируется здесь, а не в `app/di`.
    """

    @provide(scope=Scope.APP)
    def idempotency_guard(self, redis: Redis) -> IdempotencyGuard:
        """Гарда идемпотентности поверх клиента Redis из APP-скоупа."""
        return RedisIdempotencyGuard(redis)


async def _run() -> None:
    """Поднять консьюмер и работать до сигнала остановки."""
    container: AsyncContainer = make_container(TransportProvider())
    broker: RabbitBroker = await container.get(RabbitBroker)
    enrich.register(broker)
    setup_dishka(container, broker=broker, auto_inject=True)

    await broker.start()
    try:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        try:
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # Windows: Ctrl+C обрабатывается asyncio.run
            logger.debug("signal_handlers_not_supported")

        logger.info("consumer_started: queue=%s", ENRICH_QUEUE.name)
        await stop.wait()
    finally:
        await broker.stop()
        await container.close()


def main() -> None:
    """Точка входа процесса."""
    logging.basicConfig(level=logging.INFO)
    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        logger.info("consumer_stopped_by_keyboard")


if __name__ == "__main__":
    main()
