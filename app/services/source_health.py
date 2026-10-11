"""Здоровье источников и circuit breaker (ТЗ п. 5.7, 6.8)."""

from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import SourceHealth


async def record_success(
    session: AsyncSession, source_name: str, *, latency_ms: int, fetched: int
) -> None:
    health = await session.get(SourceHealth, source_name)
    if health is None:
        health = SourceHealth(source_name=source_name)
        session.add(health)
    health.last_success_at = datetime.now(UTC)
    health.error_streak = 0
    health.last_error = None
    health.total_fetched += fetched
    # скользящее среднее латентности
    if health.avg_latency_ms is None:
        health.avg_latency_ms = latency_ms
    else:
        health.avg_latency_ms = int(health.avg_latency_ms * 0.8 + latency_ms * 0.2)
    await session.commit()


async def record_error(session: AsyncSession, source_name: str, error: str) -> None:
    health = await session.get(SourceHealth, source_name)
    if health is None:
        health = SourceHealth(source_name=source_name)
        session.add(health)
    health.last_error_at = datetime.now(UTC)
    health.error_streak += 1
    health.last_error = error[:2000]
    await session.commit()


async def should_skip_source(session: AsyncSession, source_name: str) -> bool:
    """Circuit breaker (ТЗ п. 6.8): >= 5 ошибок подряд и ошибка свежее 10 минут."""
    health = await session.get(SourceHealth, source_name)
    if health is None:
        return False
    if health.error_streak >= 5:
        cooldown = timedelta(minutes=10)
        if (
            health.last_error_at
            and datetime.now(UTC) - health.last_error_at < cooldown
        ):
            return True
    return False
