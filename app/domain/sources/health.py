"""Политика circuit breaker источника (чистые функции)."""

from datetime import datetime, timedelta


CB_COOLDOWN = timedelta(minutes=10)


def circuit_open(
    streak: int,
    last_error_at: datetime | None,
    now: datetime,
    threshold: int,
    cooldown: timedelta = CB_COOLDOWN,
) -> bool:
    """Открыт ли breaker: >= threshold ошибок и ошибка свежее cooldown."""
    if streak < threshold or last_error_at is None:
        return False
    return now - last_error_at < cooldown


def next_avg_latency(avg: int | None, latency_ms: int) -> int:
    """Скользящее среднее 0.8/0.2.

    Первое измерение становится средним, дальше — экспоненциальное сглаживание.
    """
    if avg is None:
        return latency_ms
    return int(avg * 0.8 + latency_ms * 0.2)
