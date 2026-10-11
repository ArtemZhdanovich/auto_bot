"""Алерты в Telegram (этап 8, ТЗ п. 10): DLQ, circuit breaker'ы, health.

Отправка в ADMIN_CHAT_ID (fallback MANAGER_CHAT_ID) через Bot API.
Дедуп: Redis `alert:{key}` SET NX EX — повтор в течение
alert_dedup_ttl_sec подавляется (несколько воркеров не задвоят алерт).
Ошибки алертинга никогда не ломают бизнес-логику (логируем и глотаем).
"""

import asyncio
import logging
from collections.abc import Coroutine
from typing import Any

import httpx
from redis.asyncio import Redis

from app.core.config import get_settings

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org"

_redis: Redis | None = None


def _get_redis() -> Redis | None:
    """Ленивый синглтон Redis для дедупа алертов (ошибка — None)."""
    global _redis
    if _redis is None:
        try:
            _redis = Redis.from_url(get_settings().redis_url, decode_responses=True)
        except Exception:  # noqa: BLE001 — нет Redis: шлём без дедупа
            logger.warning("alert_redis_unavailable", exc_info=True)
            return None
    return _redis


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


async def _send_telegram(bot_token: str, chat_id: int, text: str) -> bool:
    url = f"{TELEGRAM_API}/bot{bot_token}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                url, json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
            )
        if resp.status_code != 200:
            logger.error(
                "alert_send_bad_status: %s %s", resp.status_code, resp.text[:200]
            )
            return False
        return True
    except httpx.HTTPError as exc:
        logger.error("alert_send_failed: %s", exc)
        return False


async def send_alert(
    text: str,
    *,
    dedup_key: str | None = None,
    dedup_ttl: int | None = None,
) -> bool:
    """Отправить алерт в Telegram. dedup_key подавляет повторы на TTL.

    Возвращает True, если алерт отправлен (или прошёл дедуп-проверку).
    """
    settings = get_settings()
    if not settings.alerts_enabled:
        return False
    chat_id = settings.admin_chat_id or settings.manager_chat_id
    if chat_id is None or not settings.bot_token:
        logger.info("alert_skipped_no_channel: %s", text[:120])
        return False

    if dedup_key:
        redis = _get_redis()
        if redis is not None:
            base_ttl = dedup_ttl or settings.alert_dedup_ttl_sec
            # эскалация: каждый повторный алерт глушится вдвое дольше
            # (30 мин → 1 ч → 2 ч → далее cap 2 ч), счётчик живёт сутки
            prev = await redis.get(f"alert_count:{dedup_key}")
            ttl = min(base_ttl * (2 ** int(prev or 0)), 2 * 3600)
            got = await redis.set(f"alert:{dedup_key}", "1", nx=True, ex=ttl)
            if not got:
                return False  # недавно уже отправляли
            sent = await _send_telegram(settings.bot_token, chat_id, text)
            if sent:
                await redis.setex(
                    f"alert_count:{dedup_key}", 24 * 3600, int(prev or 0) + 1
                )
            return sent

    return await _send_telegram(settings.bot_token, chat_id, text)
