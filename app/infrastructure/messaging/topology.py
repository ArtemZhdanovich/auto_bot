"""Топология RabbitMQ: exchange `tasks`, рабочие quorum-очереди и DLX.

Имена очередей совпадают с прежними (`app/queue/rabbit.py`), поэтому новый
воркер можно катить рядом со старым: те же routing key попадают в ту же
очередь.

Рабочие очереди — quorum: `x-delivery-limit` считает доставки, и после
лимита сообщение уходит в `tasks.dlx` (fanout) → `dead_letter`, а не теряется.
"""

from __future__ import annotations

from faststream.rabbit import ExchangeType, RabbitExchange, RabbitQueue

TASKS_EXCHANGE = RabbitExchange("tasks", type=ExchangeType.DIRECT, durable=True)
DLX_EXCHANGE = RabbitExchange("tasks.dlx", type=ExchangeType.FANOUT, durable=True)

DEAD_LETTER_QUEUE = RabbitQueue("dead_letter", durable=True)


def _work_queue(name: str, routing_key: str) -> RabbitQueue:
    """Рабочая quorum-очередь с лимитом доставок и DLX.

    `x-queue-type` задаётся аргументом, а не `queue_type`: FastStream
    подставляет значение из `queue_type` только как значение по умолчанию,
    а в declare уходят именно `arguments`.
    """
    return RabbitQueue(
        name,
        durable=True,
        routing_key=routing_key,
        arguments={
            "x-queue-type": "quorum",
            "x-delivery-limit": 5,
            "x-dead-letter-exchange": DLX_EXCHANGE.name,
        },
    )


FETCH_QUEUE = _work_queue("fetch", "fetch_source_task")
SEND_QUEUE = _work_queue("send", "send_listing_notification")
ENRICH_QUEUE = _work_queue("enrich", "enrich_listing")
