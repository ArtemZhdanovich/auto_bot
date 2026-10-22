"""Здоровье источников и circuit breaker (ТЗ п. 5.7, 6.8)."""

from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.infrastructure.db.models import SourceHealth
from app.domain.sources.health import CB_COOLDOWN, circuit_open, next_avg_latency
from app.infrastructure.services.alerting import fire, send_alert


def _cb_streak() -> int:
    """Порог CB: N ошибок подряд (env SOURCE_CB_ERROR_STREAK, по умолчанию 5)."""
    return get_settings().source_cb_error_streak


CB_COOLDOWN = timedelta(minutes=10)  # пауза после открытия


async def record_success(
    session: AsyncSession, source_name: str, *, latency_ms: int, fetched: int
) -> None:
    health = await session.get(SourceHealth, source_name)
    if health is None:
        health = SourceHealth(source_name=source_name)
        # default= у колонок применяется только при INSERT — до flush атрибуты
        # None, поэтому инициализируем явно (иначе total_fetched += упадёт)
        health.error_streak = 0
        health.total_fetched = 0
        session.add(health)
    prev_streak = health.error_streak or 0
    health.last_success_at = datetime.now(UTC)
    health.error_streak = 0
    health.last_error = None
    health.total_fetched = (health.total_fetched or 0) + fetched
    # скользящее среднее латентности
    health.avg_latency_ms = next_avg_latency(health.avg_latency_ms, latency_ms)
    await session.commit()
    if prev_streak >= _cb_streak():  # восстановление после открытия CB
        fire(
            send_alert(
                f"🟢 Источник <b>{source_name}</b> восстановился "
                f"(был circuit breaker, streak={prev_streak}).",
                dedup_key=f"cb_recovery:{source_name}",
            )
        )


async def record_error(session: AsyncSession, source_name: str, error: str) -> None:
    health = await session.get(SourceHealth, source_name)
    if health is None:
        health = SourceHealth(source_name=source_name)
        health.error_streak = 0  # см. record_success: дефолт только при INSERT
        session.add(health)
    health.last_error_at = datetime.now(UTC)
    health.error_streak = (health.error_streak or 0) + 1
    health.last_error = error[:2000]
    await session.commit()
    if health.error_streak == _cb_streak():  # момент открытия CB
        fire(
            send_alert(
                f"🔴 Circuit breaker: источник <b>{source_name}</b> отключён "
                f"на {CB_COOLDOWN} ({_cb_streak()} ошибок подряд): "
                f"{error[:200]}",
                dedup_key=f"cb:{source_name}",
            )
        )


async def should_skip_source(session: AsyncSession, source_name: str) -> bool:
    """Circuit breaker (ТЗ п. 6.8): >= N ошибок подряд и ошибка свежее cooldown."""
    health = await session.get(SourceHealth, source_name)
    if health is None:
        return False
    return circuit_open(
        health.error_streak or 0,
        health.last_error_at,
        datetime.now(UTC),
        _cb_streak(),
        CB_COOLDOWN,
    )

