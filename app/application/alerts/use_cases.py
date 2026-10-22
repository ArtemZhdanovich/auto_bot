"""Сценарий алерта: дедуп с эскалацией окна подавления.

Ключи сохранены как в прежней реализации (их читают дашборды и живой Redis):
- `alert:{key}` — окно подавления (`SET NX EX`), повтор внутри окна глушится;
- `alert_count:{key}` — счётчик эскалации на сутки: каждый повторный алерт
  глушится вдвое дольше (30 мин → 1 ч → 2 ч, cap 2 ч).

Порты: `NotifierPort` (доставка) и `CachePort` (дедуп). Ошибки кэша не
ломают алерт — fail-open: без дедупа отправляем, лучше дубль, чем тишина.
Доставка не критична: сценарий возвращает `bool`, вызывающий (`fire`)
гасит исключения в лог.
"""

from __future__ import annotations

import logging

from app.application.ports import CachePort, NotifierPort

logger = logging.getLogger(__name__)

DEDUP_TTL_SEC = 30 * 60
DEDUP_COUNT_TTL_SEC = 24 * 3600
ESCALATION_CAP_SEC = 2 * 3600


def dedup_window(attempt: int, base_ttl: int) -> int:
    """TTL подавления: base × 2^attempt, но не больше 2 часов."""
    return min(base_ttl * (2 ** max(attempt, 0)), ESCALATION_CAP_SEC)


async def _claim(
    cache: CachePort, dedup_key: str, base_ttl: int
) -> tuple[bool, int] | None:
    """Занять окно подавления атомарно; `None` — кэш недоступен (fail-open)."""
    try:
        raw = await cache.get(f"alert_count:{dedup_key}")
        attempt = int(raw) if raw else 0
        claimed = await cache.set_if_absent(
            f"alert:{dedup_key}", "1", dedup_window(attempt, base_ttl)
        )
        return bool(claimed), attempt
    except Exception:  # noqa: BLE001 — fail-open: шлём без дедупа
        logger.warning("alert_dedup_failed: %s", dedup_key, exc_info=True)
        return None


async def send_alert(
    text: str,
    *,
    notifier: NotifierPort,
    chat_id: int,
    cache: CachePort | None = None,
    dedup_key: str | None = None,
    dedup_ttl: int | None = None,
    default_ttl: int = DEDUP_TTL_SEC,
) -> bool:
    """Отправить алерт; `dedup_key` подавляет повторы на TTL.

    Возвращает `True`, если сообщение ушло (или повтор подавлен дедупом —
    как и раньше, это «алерт уже доставлен недавно»).
    """
    if dedup_key and cache is not None:
        claim = await _claim(cache, dedup_key, dedup_ttl or default_ttl)
        if claim is not None:
            claimed, attempt = claim
            if not claimed:
                return False  # недавно уже отправляли
            sent = await notifier.send_text(chat_id, text)
            if sent:
                try:
                    await cache.set(
                        f"alert_count:{dedup_key}", str(attempt + 1), DEDUP_COUNT_TTL_SEC
                    )
                except Exception:  # noqa: BLE001 — счётчик не критичен
                    logger.warning(
                        "alert_dedup_count_failed: %s", dedup_key, exc_info=True
                    )
            return sent

    return await notifier.send_text(chat_id, text)
