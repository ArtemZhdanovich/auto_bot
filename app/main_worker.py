"""Воркер outbox-релея.

Запуск:
    python -m app.main_worker            # цикл до SIGINT/SIGTERM
    python -m app.main_worker --once     # один проход (smoke/CI)

Релей публикует события из таблицы `outbox` после commit. Отдельный процесс,
чтобы API не зависел от доступности брокера.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal

from dishka import AsyncContainer
from faststream.rabbit import RabbitBroker
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.di.container import make_container
from app.infrastructure.messaging.publisher import RawEventPublisher
from app.infrastructure.messaging.relay import OutboxRelay

logger = logging.getLogger(__name__)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Outbox relay worker")
    parser.add_argument(
        "--once",
        action="store_true",
        help="опубликовать одну пачку и выйти",
    )
    return parser.parse_args(argv)


async def _run(once: bool) -> None:
    """Поднять брокер, прогнать релей и корректно закрыть ресурсы."""
    container: AsyncContainer = make_container()
    broker: RabbitBroker = await container.get(RabbitBroker)
    await broker.start()
    try:
        session_factory = await container.get(async_sessionmaker[AsyncSession])
        publisher = await container.get(RawEventPublisher)
        relay = OutboxRelay(session_factory, publisher)

        if once:
            published = await relay.drain_once()
            logger.info("relay_once published=%d", published)
            return

        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        try:
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # Windows: Ctrl+C обрабатывается asyncio.run
            logger.debug("signal_handlers_not_supported")

        task = asyncio.create_task(relay.run(stop))
        await stop.wait()
        stop.set()
        await task
    finally:
        await broker.stop()
        await container.close()


def main(argv: list[str] | None = None) -> None:
    """Точка входа процесса."""
    logging.basicConfig(level=logging.INFO)
    args = _parse_args(argv)
    try:
        asyncio.run(_run(args.once))
    except KeyboardInterrupt:
        logger.info("relay_stopped_by_keyboard")


if __name__ == "__main__":
    main()
