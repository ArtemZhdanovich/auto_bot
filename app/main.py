"""Точка входа Backend (FastAPI). Запуск: uvicorn app.main:app (ТЗ п. 4.3)."""

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import (
    admin,
    ai_search,
    auth,
    dialog,
    favorites,
    filters,
    listings,
    me,
    notifications,
    payments,
    users,
)
from app.core.config import get_settings
from app.core.deps import SessionDep
from app.db.models import Listing, SentLog, SourceHealth, User, UserFilter
from app.db.session import session_factory

settings = get_settings()

logging.basicConfig(level=settings.log_level)

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Auto Selection Ecosystem API",
    version="0.1.0",
    description="Backend для Telegram-бота и Mini App (ТЗ п. 4.3)",
)


@app.get("/health", tags=["system"])
async def health() -> dict[str, str]:
    return {"status": "ok", "env": settings.app_env}


# --- health detailed / metrics (этап 8, ТЗ п. 10) ---------------------------


async def _check_db() -> bool:
    try:
        async with session_factory() as session:
            await session.execute(text("SELECT 1"))
        return True
    except Exception:  # noqa: BLE001 — healthcheck не бросает
        logger.warning("health_db_failed", exc_info=True)
        return False


async def _check_redis() -> bool:
    try:
        from redis.asyncio import Redis

        client = Redis.from_url(settings.redis_url, decode_responses=True)
        try:
            return bool(await client.ping())
        finally:
            await client.aclose()
    except Exception:  # noqa: BLE001
        logger.warning("health_redis_failed", exc_info=True)
        return False


async def _check_rabbit() -> bool:
    try:
        import aio_pika

        connection = await aio_pika.connect(settings.rabbitmq_url)
        await connection.close()
        return True
    except Exception:  # noqa: BLE001
        logger.warning("health_rabbit_failed", exc_info=True)
        return False


@app.get("/health/detailed", tags=["system"])
async def health_detailed() -> JSONResponse:
    """Здоровье зависимостей: postgres, redis, rabbitmq (этап 8).

    503, если недоступна БД (критично); остальные компоненты — только статус.
    """
    db_ok, redis_ok, rabbit_ok = await asyncio.gather(
        _check_db(), _check_redis(), _check_rabbit()
    )
    return JSONResponse(
        status_code=200 if db_ok else 503,
        content={
            "status": "ok" if db_ok else "degraded",
            "env": settings.app_env,
            "components": {
                "postgres": db_ok,
                "redis": redis_ok,
                "rabbitmq": rabbit_ok,
            },
        },
    )


async def _scalar(session: AsyncSession, stmt: Any) -> int | None:
    try:
        return (await session.execute(stmt)).scalar_one()
    except Exception:  # noqa: BLE001 — метрика не может ломать эндпоинт
        return None


@app.get("/metrics", tags=["system"])
async def metrics(session: SessionDep) -> dict[str, Any]:
    """Базовые метрики для мониторинга (этап 8): пользователи, объявления,
    уведомления, health источников. Счётчики: агрегаты, без персональных данных."""
    day_ago = datetime.now(UTC) - timedelta(hours=24)

    users_total = await _scalar(session, select(func.count()).select_from(User))
    users_new_24h = await _scalar(
        session,
        select(func.count()).select_from(User).where(User.created_at >= day_ago),
    )
    filters_total = await _scalar(
        session, select(func.count()).select_from(UserFilter)
    )
    listings_total = await _scalar(
        session, select(func.count()).select_from(Listing)
    )
    listings_new_24h = await _scalar(
        session,
        select(func.count())
        .select_from(Listing)
        .where(Listing.updated_at >= day_ago),
    )
    notifications_sent_24h = await _scalar(
        session,
        select(func.count()).select_from(SentLog).where(SentLog.sent_at >= day_ago),
    )

    try:
        result = await session.execute(
            select(User.subscription_tier, func.count())
            .select_from(User)
            .group_by(User.subscription_tier)
        )
        users_by_tier = {tier: int(n) for tier, n in result.all()}
    except Exception:  # noqa: BLE001
        users_by_tier = {}

    sources: list[dict[str, Any]] = []
    try:
        result = await session.execute(select(SourceHealth))
        for health in result.scalars().all():
            sources.append(
                {
                    "source_name": health.source_name,
                    "avg_latency_ms": health.avg_latency_ms,
                    "error_streak": health.error_streak,
                    "last_success_at": (
                        health.last_success_at.isoformat()
                        if health.last_success_at
                        else None
                    ),
                }
            )
    except Exception:  # noqa: BLE001
        pass

    return {
        "users": {
            "total": users_total,
            "new_24h": users_new_24h,
            "by_tier": users_by_tier,
        },
        "filters": {"total": filters_total},
        "listings": {"total": listings_total, "new_24h": listings_new_24h},
        "notifications_sent_24h": notifications_sent_24h,
        "sources": sources,
    }


app.include_router(auth.router)
app.include_router(users.router)
app.include_router(me.router)
app.include_router(filters.router)
app.include_router(listings.router)
app.include_router(favorites.router)
app.include_router(payments.router)
app.include_router(admin.router)
app.include_router(dialog.router)
app.include_router(ai_search.router)
app.include_router(notifications.router)
