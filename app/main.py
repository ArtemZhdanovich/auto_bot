"""Точка входа Backend (FastAPI). Запуск: uvicorn app.main:app (ТЗ п. 4.3)."""

import asyncio
import logging

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api import (
    admin,
    ai_search,
    auth,
    calc,
    dialog,
    favorites,
    filters,
    leads,
    listings,
    me,
    metrics,
    notifications,
    partners,
    payments,
    users,
)
from app.core.config import get_settings
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


app.include_router(auth.router)
app.include_router(users.router)
app.include_router(me.router)
app.include_router(metrics.router)  # GET /metrics (этап 8) — общий роутер с admin
app.include_router(filters.router)
app.include_router(listings.router)
app.include_router(calc.router)
app.include_router(favorites.router)
app.include_router(payments.router)
app.include_router(admin.router)
app.include_router(dialog.router)
app.include_router(ai_search.router)
app.include_router(notifications.router)
app.include_router(leads.router)
app.include_router(partners.admin_router)
app.include_router(partners.router)
