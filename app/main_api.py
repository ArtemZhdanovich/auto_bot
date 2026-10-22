"""Точка входа Backend (FastAPI). Запуск: uvicorn app.main_api:app (ТЗ п. 4.3)."""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.transport.http.routers import (
    admin,
    ai_search,
    analysis,
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
from app.config import get_settings
from app.transport.http.middleware.trace import TraceIdMiddleware, install_trace_logging
from app.infrastructure.db.session import dispose_engine, session_factory, warmup_engine

settings = get_settings()

logging.basicConfig(level=settings.log_level)
install_trace_logging()

logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Прогрев пула на старте и закрытие соединений на shutdown."""
    await warmup_engine()
    from faststream.rabbit import RabbitBroker

    broker = await _dishka_container.get(RabbitBroker)
    await broker.start()
    yield
    await broker.stop()
    await _dishka_container.close()
    await dispose_engine()


app = FastAPI(
    title="Auto Selection Ecosystem API",
    version="0.1.0",
    description="Backend для Telegram-бота и Mini App (ТЗ п. 4.3)",
    lifespan=lifespan,
)

app.add_middleware(TraceIdMiddleware)

# Dishka-контейнер: единая сборка графа (сессия → репозитории → сценарии).
# FastAPI `Depends` не используется вовсе: use case-ы роутеры получают через
# `FromDishka` (route_class=DishkaRoute), аутентификация — dishka-провайдеры
# (`HttpDepsProvider`, Request из dishka-контекста), внутренние API —
# `InternalDishkaRoute` (X-Internal-Token до выполнения эндпоинта).
from dishka.integrations.fastapi import setup_dishka  # noqa: E402

from app.di.container import make_container  # noqa: E402

_dishka_container = make_container()
setup_dishka(container=_dishka_container, app=app)


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


# TECHDEBT 12.15: health check не должен создавать соединения на каждый
# запрос — клиенты переиспользуются (Redis сам переподключается).
_health_redis = None
_health_rabbit = None


async def _check_redis() -> bool:
    global _health_redis
    try:
        from redis.asyncio import Redis

        if _health_redis is None:
            _health_redis = Redis.from_url(
                settings.redis_url,
                decode_responses=True,
                socket_timeout=5.0,
                socket_connect_timeout=5.0,
            )
        return bool(await _health_redis.ping())
    except Exception:  # noqa: BLE001
        logger.warning("health_redis_failed", exc_info=True)
        return False


async def _check_rabbit() -> bool:
    global _health_rabbit
    try:
        import aio_pika

        if _health_rabbit is None or _health_rabbit.is_closed:
            # connect_robust: разорванная связь восстанавливается сама
            _health_rabbit = await aio_pika.connect_robust(settings.rabbitmq_url)
        return not _health_rabbit.is_closed
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
app.include_router(analysis.router)
app.include_router(analysis.bot_router)
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
