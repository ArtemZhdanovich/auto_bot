"""Фиксированное окно (60s) rate limit для партнёрского API.

INCR+EXPIRE не атомарен: при гонке возможен «на 1 больше», но не обход
лимита. Redis down — fail-open: rate limit не должен ронять API.
Транспорт получает `bool` и сам маппит отказ в 429 — здесь нет HTTP.
"""

from __future__ import annotations

import logging
from typing import Any

from app.application.partners.ports import PartnerRateLimiter
from app.config import Settings

logger = logging.getLogger(__name__)

_RATE_LIMIT_PREFIX = "partner:rl"
_RATE_LIMIT_WINDOW_SEC = 60


async def check_rate_limit(
    redis: Any, partner_id: int, *, limit: int, window_sec: int = _RATE_LIMIT_WINDOW_SEC
) -> bool:
    """Фиксированное окно `window_sec`: не более `limit` запросов на партнёра.

    Args:
        redis: Async Redis-клиент.
        partner_id: Идентификатор партнёра (ключ окна).
        limit: Максимум запросов за окно (из `Settings`).
        window_sec: Ширина окна в секундах.

    Returns:
        `True` — запрос в лимите (или Redis недоступен — fail-open);
        `False` — лимит превышен.
    """
    key = f"{_RATE_LIMIT_PREFIX}:{partner_id}"
    try:
        count = await redis.incr(key)
        if count == 1:
            await redis.expire(key, window_sec)
    except Exception:  # noqa: BLE001 — Redis down → fail-open
        logger.warning("partner_rate_limit_redis_down", exc_info=True)
        return True
    return count <= limit


class RedisRateLimiter(PartnerRateLimiter):
    """Порт `PartnerRateLimiter` → фиксированное окно на Redis.

    Лимит — `Settings.partner_rate_limit_per_min`; отказ Redis — fail-open.
    """

    def __init__(self, redis: Any, settings: Settings) -> None:
        self._redis = redis
        self._settings = settings

    async def check(self, partner_id: int) -> bool:
        """`True` — запрос в лимите (или Redis недоступен)."""
        return await check_rate_limit(
            self._redis,
            partner_id,
            limit=self._settings.partner_rate_limit_per_min,
        )
