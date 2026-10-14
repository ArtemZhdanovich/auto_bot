"""Общая фабрика доменных сервисов (shared kernel — app/*, сервис — тонкая обёртка)."""

import logging
from collections.abc import Iterable
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.core.config import get_settings


def create_domain_service(
    name: str, version: str, routers: Iterable[object]
) -> FastAPI:
    """FastAPI-приложение сервиса: /health + роутеры своего домена.

    Роутеры переиспользуются из монолита (app/api/*) без копирования —
    один и тот же код крутится в режиме `mono` (app/main.py) и в
    режиме `micro` (отдельный процесс на сервис).
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        logging.basicConfig(level=get_settings().log_level)
        yield

    app = FastAPI(
        title=f"auto_bot {name}",
        version=version,
        description=f"Доменный сервис `{name}` (профиль micro)",
        lifespan=lifespan,
    )

    @app.get("/health", tags=["system"])
    async def health() -> dict[str, str]:
        """Liveness сервиса (пингуется gateway'ем в /health/detailed)."""
        return {"status": "ok", "service": name, "env": get_settings().app_env}

    for router in routers:
        app.include_router(router)
    return app
