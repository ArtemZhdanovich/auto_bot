"""Replay DLQ (TECHDEBT 13.3): python -m app.infrastructure.messaging.replay.

`dead_letter` копится, а разобрать её раньше было нечем. CLI читает
сообщения, декодирует job из тела (routing key = имя job) и ставит
обратно в exchange `tasks`. ack — только после успешного publish
(at-least-once); битые (недекодируемые) сообщения удаляются с логом —
иначе они вечно крутятся в голове очереди.

Usage:
    python -m app.infrastructure.messaging.replay --limit 100 [--dry-run]
"""

from __future__ import annotations

import argparse
import asyncio
import logging

import aio_pika
from aio_pika import DeliveryMode, ExchangeType

from app.config import get_settings
from app.infrastructure.messaging.legacy_worker import decode_message

logger = logging.getLogger(__name__)


async def replay(limit: int = 100, dry_run: bool = False) -> int:
    """Перенести до `limit` сообщений из `dead_letter` обратно в `tasks`.

    Args:
        limit: Максимум сообщений за прогон (не опустошаем DLQ целиком —
            повторный прогон на среде безопаснее взрыва ретраев).
        dry_run: Только показать содержимое, ничего не переотправлять.

    Returns:
        Сколько сообщений обработано (ack сделан).
    """
    settings = get_settings()
    connection = await aio_pika.connect_robust(settings.rabbitmq_url)
    handled = 0
    try:
        channel = await connection.channel()
        dlq = await channel.declare_queue("dead_letter", durable=True)
        exchange = await channel.declare_exchange(
            "tasks", ExchangeType.DIRECT, durable=True
        )
        for _ in range(limit):
            message = await dlq.get(fail=False)
            if message is None:
                break
            try:
                decoded = decode_message(message.body)
                job = decoded["job"]
                if dry_run:
                    print(f"{job} args={decoded.get('args')}")
                else:
                    await exchange.publish(
                        aio_pika.Message(
                            body=message.body,
                            delivery_mode=DeliveryMode.PERSISTENT,
                        ),
                        routing_key=job,
                    )
                    logger.info("dlq_replayed: job=%s", job)
                await message.ack()
                handled += 1
            except Exception:  # noqa: BLE001 — poison-сообщение не должно
                # блокировать очередь: лог + удаление
                logger.exception("dlq_poison_message_dropped")
                await message.reject(requeue=False)
        return handled
    finally:
        await connection.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Replay сообщений из dead_letter обратно в рабочие очереди."
    )
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument(
        "--dry-run", action="store_true", help="показать, не переотправляя"
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    handled = asyncio.run(replay(args.limit, args.dry_run))
    print(f"dlq_replay_done: handled={handled}")


if __name__ == "__main__":
    main()
