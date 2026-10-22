"""Консьюмер очереди `enrich`: догрузка спеков лота из detail-источника.

Очередь `enrich` — рабочая quorum-очередь с `x-delivery-limit=5` и DLX
(см. `app/infrastructure/messaging/topology.py`), поэтому обработчик обязан
быть идемпотентным: повторную доставку отсекает ключ в Redis, а не ack.
"""

from __future__ import annotations

import logging

from dishka_faststream import FromDishka
from faststream.rabbit import RabbitBroker
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.shared.ports import IdempotencyGuard
from app.infrastructure.db.models import Listing
from app.infrastructure.messaging.topology import ENRICH_QUEUE, TASKS_EXCHANGE
from app.infrastructure.services.fetch import enrich_listing_specs
from app.transport.messaging.contracts import EnrichListingCommand

logger = logging.getLogger(__name__)

# Спек лота не меняется: сутки не нужны, но и 7 дней ключа хватает, чтобы
# отсечь повторные доставки при ретраях и redelivery из DLQ.
IDEMPOTENCY_TTL_SEC = 7 * 24 * 60 * 60
IDEMPOTENCY_KEY_TEMPLATE = "idem:enrich:{key}"


def register(broker: RabbitBroker) -> None:
    """Подписать брокер на очередь `enrich`.

    Args:
        broker: Брокер процесса; подписка создаётся один раз до `start()`.
    """

    @broker.subscriber(queue=ENRICH_QUEUE, exchange=TASKS_EXCHANGE)
    async def handle_enrich_listing(
        cmd: EnrichListingCommand,
        session: FromDishka[AsyncSession],
        guard: FromDishka[IdempotencyGuard],
    ) -> None:
        """Обогатить лот, пропустив повторную доставку.

        Args:
            cmd: Валидированная команда из очереди.
            session: Сессия REQUEST-скоупа (dishka открывает её на сообщение).
            guard: Защита от повторной доставки.

        Raises:
            Exception: Любая ошибка обогащения — ключ идемпотентности
                освобождается, сообщение уходит на повторную доставку.
        """
        key = IDEMPOTENCY_KEY_TEMPLATE.format(key=cmd.idempotency_key)
        if not await guard.acquire(key, ttl_sec=IDEMPOTENCY_TTL_SEC):
            logger.info(
                "enrich_skipped_duplicate: listing_id=%s key=%s",
                cmd.listing_id,
                cmd.idempotency_key,
            )
            return

        try:
            listing = await session.get(Listing, cmd.listing_id)
            if listing is None:
                # Лот удалён/ещё не закоммичен: повтор не поможет, ключ не снимаем.
                logger.warning(
                    "enrich_skipped_missing_listing: listing_id=%s",
                    cmd.listing_id,
                )
                return

            updated = await enrich_listing_specs(session, listing)
            logger.info(
                "enrich_done: listing_id=%s updated=%s",
                cmd.listing_id,
                updated,
            )
        except Exception:
            await guard.release(key)
            raise
