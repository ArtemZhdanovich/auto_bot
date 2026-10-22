"""Общая фабрика доменных сервисов (shared kernel — app/*, сервис — тонкая обёртка)."""

import logging
from collections.abc import Iterable
from contextlib import asynccontextmanager

from dishka import AsyncContainer
from dishka.integrations.fastapi import setup_dishka
from fastapi import FastAPI

from app.config import get_settings
from app.di.container import make_container


def create_domain_service(
    name: str, version: str, routers: Iterable[object]
) -> FastAPI:
    """FastAPI-приложение сервиса: /health + роутеры своего домена.

    Роутеры переиспользуются из монолита (app/transport/http/routers/*)
    без копирования — один и тот же код крутится в режиме `mono`
    (app/main_api.py) и в режиме `micro` (отдельный процесс на сервис).
    Сборка зависимостей — общий Dishka-контейнер (`app/di`).

    Args:
        name: Имя сервиса (заголовок и health).
        version: Версия сервиса.
        routers: Роутеры домена.

    Returns:
        Настроенное FastAPI-приложение.
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        logging.basicConfig(level=get_settings().log_level)
        container: AsyncContainer = app.state.dishka_container
        try:
            yield
        finally:
            await container.close()

    app = FastAPI(
        title=f"auto_bot {name}",
        version=version,
        description=f"Доменный сервис `{name}` (профиль micro)",
        lifespan=lifespan,
    )

    container = make_container()
    setup_dishka(container, app)

    @app.get("/health", tags=["system"])
    async def health() -> dict[str, str]:
        """Liveness сервиса (пингуется gateway'ем в /health/detailed)."""
        return {"status": "ok", "service": name, "env": get_settings().app_env}

    for router in routers:
        app.include_router(router)
    return app
