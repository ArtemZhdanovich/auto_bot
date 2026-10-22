"""Фасад алертов (обратная совместимость): настройки → порты.

Сценарий (дедуп ключей, эскалация окна, fail-open) — `application/alerts/`,
доставка — `infrastructure/messaging/telegram.py`, дедуп-кэш —
`infrastructure/cache/redis.py`. Здесь только сборка:

- отправка в ADMIN_CHAT_ID (fallback MANAGER_CHAT_ID) через Bot API;
- дедуп: Redis `alert:{key}` SET NX EX — повтор в течение
  alert_dedup_ttl_sec подавляется (несколько воркеров не задвоят алерт);
- ошибки алертинга никогда не ломают бизнес-логику (логируем и глотаем).
"""

import asyncio
import logging
from collections.abc import Coroutine
from typing import Any

from redis.asyncio import Redis

from app.application.alerts.use_cases import send_alert as _send_alert
from app.config import get_settings
from app.infrastructure.cache.redis import RedisCache
from app.infrastructure.messaging.telegram import TelegramNotifier

__all__ = ["TelegramNotifier", "fire", "send_alert"]

logger = logging.getLogger(__name__)

_redis: Redis | None = None

def _get_redis() -> Redis | None:
    """Ленивый клиент Redis для дедупа алертов (ошибка — None).

    Не хранит кэш на уровне модуля — нет скрытого глобального состояния.
    """
    try:
        return Redis.from_url(get_settings().redis_url, decode_responses=True)
    except Exception:  # noqa: BLE001 — нет Redis: шлём без дедупа
        logger.warning("alert_redis_unavailable", exc_info=True)
        return None


def fire(coro: Coroutine[Any, Any, Any]) -> None:
    """Fire-and-forget запуск алерта: ошибки пишутся в лог, не в бизнес-путь."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        coro.close()  # нет event loop (например, импорт вне воркера)
        return
    task = loop.create_task(coro)

    def _done(t: asyncio.Task[Any]) -> None:
        if t.cancelled():
            return
        exc = t.exception()
        if exc is not None:
            logger.warning("alert_fire_failed: %s", exc)

    task.add_done_callback(_done)


async def send_alert(
    text: str,
    *,
    dedup_key: str | None = None,
    dedup_ttl: int | None = None,
    redis: Any | None = None,
) -> bool:
    """Отправить алерт в Telegram. dedup_key подавляет повторы на TTL.

    redis — необязательная точка инъекции клиента дедупликации (тесты,
    воркеры со своим пулом); без неё берётся общий клиент из настроек.
    Возвращает True, если алерт отправлен (или прошёл дедуп-проверку).
    """
    settings = get_settings()
    if not settings.alerts_enabled:
        return False
    chat_id = settings.admin_chat_id or settings.manager_chat_id
    if chat_id is None or not settings.bot_token:
        logger.info("alert_skipped_no_channel: %s", text[:120])
        return False

    client = redis if redis is not None else _get_redis()
    return await _send_alert(
        text,
        notifier=TelegramNotifier(settings.bot_token.get_secret_value()),
        chat_id=chat_id,
        cache=RedisCache(client) if client is not None else None,
        dedup_key=dedup_key,
        dedup_ttl=dedup_ttl,
        default_ttl=settings.alert_dedup_ttl_sec,
    )


class TelegramAlertPort:
    """`AlertPort` прикладного слоя поверх `send_alert`.

    `redis` — клиент дедупликации: воркер передаёт свой пул, тесты — стаб.
    """

    def __init__(self, redis: Any | None = None) -> None:
        self._redis = redis

    async def send(self, text: str, *, dedup_key: str | None = None) -> None:
        """Отправить алерт владельцу (повтор с тем же ключом подавляется)."""
        await send_alert(text, dedup_key=dedup_key, redis=self._redis)
