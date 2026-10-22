"""Relay outbox → RabbitMQ.

Событие пишется в outbox в транзакции бизнес-данных, relay публикует его
после commit. Гарантия — at-least-once: падение между publish и commit
даёт повторную публикацию, поэтому обработчики идемпотентны по `event_id`.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.infrastructure.db.outbox import OutboxMessage
from app.infrastructure.messaging.publisher import RawEventPublisher

logger = logging.getLogger(__name__)

DEFAULT_BATCH = 100
DEFAULT_INTERVAL = 0.5
LAST_ERROR_LIMIT = 512  # длина колонки outbox.last_error


class OutboxRelay:
    """Публикует неопубликованные строки outbox пачками."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        publisher: RawEventPublisher,
        batch: int = DEFAULT_BATCH,
        interval: float = DEFAULT_INTERVAL,
    ) -> None:
        self._session_factory = session_factory
        self._publisher = publisher
        self._batch = batch
        self._interval = interval

    async def drain_once(self) -> int:
        """Опубликовать до `batch` событий и вернуть число отправленных.

        Строки блокируются `FOR UPDATE SKIP LOCKED`, поэтому несколько relay
        не публикуют одно и то же событие. Ошибка публикации не прерывает
        пачку: попытка и текст ошибки сохраняются, строка остаётся
        неопубликованной до следующего прохода.
        """
        published = 0
        async with self._session_factory() as session:
            rows = (
                (
                    await session.execute(
                        select(OutboxMessage)
                        .where(OutboxMessage.published_at.is_(None))
                        .order_by(OutboxMessage.id)
                        .limit(self._batch)
                        .with_for_update(skip_locked=True)
                    )
                )
                .scalars()
                .all()
            )

            for message in rows:
                try:
                    await self._publisher.publish_raw(
                        message.routing_key,
                        message.payload,
                        message.event_id,
                    )
                except Exception as exc:  # noqa: BLE001 — одна строка не роняет пачку
                    message.attempts = (message.attempts or 0) + 1
                    message.last_error = str(exc)[:LAST_ERROR_LIMIT]
                    logger.warning(
                        "outbox_publish_failed: id=%s routing_key=%s attempts=%s error=%s",
                        message.id,
                        message.routing_key,
                        message.attempts,
                        exc,
                    )
                    continue

                message.published_at = datetime.now(UTC)
                published += 1

            # Commit и на успешные строки, и на attempts/last_error упавших.
            await session.commit()

        if published:
            logger.info("outbox_drained: published=%s", published)
        return published

    async def run(self, stop_event: asyncio.Event) -> None:
        """Крутить `drain_once`, пока не установлен `stop_event`.

        Сбой БД или брокера логируется и не завершает цикл: следующий проход
        повторит попытку.
        """
        while not stop_event.is_set():
            try:
                await self.drain_once()
            except Exception:  # noqa: BLE001 — цикл переживает сбой прохода
                logger.exception("outbox_drain_failed")

            try:
                await asyncio.wait_for(stop_event.wait(), timeout=self._interval)
            except TimeoutError:
                pass
