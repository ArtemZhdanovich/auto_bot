"""IP-rate-limit для незащищённых эндпоинтов (TECHDEBT 1.4).

Фиксированное окно в Redis: `INCR` + `EXPIRE` при создании ключа.
Не атомарно (в отличие от Lua-квот 5.4), но для защиты от массового
создания гостей это осознанный компромисс: в худшем случае окно
первого запроса чуть «растягивается», злоумышленнику это не помощь.

Ключ берётся из `X-Forwarded-For` (первый IP — за reverse-proxy),
fallback — `request.client.host`.
"""

from __future__ import annotations

import logging
import time

from redis.asyncio import Redis

logger = logging.getLogger(__name__)

_GUEST_LIMIT_DEFAULT = 5  # созданий сессий с одного IP в минуту
_WINDOW_SEC = 60
_PREFIX = "rl:guest"


async def guest_ip_allowed(
    redis: Redis,
    ip: str,
    *,
    limit: int = _GUEST_LIMIT_DEFAULT,
    window_sec: int = _WINDOW_SEC,
) -> bool:
    """`True`, если с этого IP ещё можно создать гостевую сессию.

    Args:
        redis: Клиент Redis (счётчик `rl:guest:{ip}:{window_number}`).
        ip: IP клиента (после normalизации X-Forwarded-For).
        limit: Максимум созданий за окно.
        window_sec: Размер окна, секунд.

    Returns:
        `False`, если лимит исчерпан (транспорт отвечает 429).
    """
    bucket = int(_now() // window_sec)
    key = f"{_PREFIX}:{ip}:{bucket}"
    try:
        count = int(await redis.incr(key))  # type: ignore[attr-defined]
        if count == 1:
            await redis.expire(key, window_sec + 5)  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 — Redis лёг: не блокируем гостей (fail-open)
        logger.warning("guest_rate_limit_unavailable", exc_info=True)
        return True
    return count <= limit


def client_ip(forwarded_for: str | None, host: str | None) -> str:
    """Извлекает IP клиента: первый из `X-Forwarded-For`, иначе host.

    Args:
        forwarded_for: Заголовок `X-Forwarded-For` или `None`.
        host: `request.client.host` или `None`.

    Returns:
        Нормализованный IP (или `"unknown"`).
    """
    if forwarded_for:
        first = forwarded_for.split(",")[0].strip()
        if first:
            return first
    return (host or "unknown").strip() or "unknown"


def _now() -> float:
    """Текущее время (вынесено для тестов)."""
    return time.time()