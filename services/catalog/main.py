"""catalog-service (8102): лента объявлений и пользовательские подборки.

Домены: /api/listings (лента, карточка, история цен), /api/favorites,
/api/filters (CRUD с тарифными лимитами), /api/notifications
(режим уведомлений и тихие часы — применяются к фильтрам),
/api/calc (калькулятор «под ключ» Корея → РФ/РБ — чистая логика,
без БД; stateless, отсюда же отдаётся в монолитном профиле).
"""

from fastapi import FastAPI

from app.transport.http.routers import analysis, calc, favorites, filters, listings, notifications
from services._base import create_domain_service

SERVICE_NAME = "catalog"


def create_app() -> FastAPI:
    return create_domain_service(
        SERVICE_NAME,
        "0.1.0",
        [
            listings.router,
            analysis.router,  # AI-анализ цены: GET/POST /api/listings/{id}/analysis
            analysis.bot_router,  # + внутренний канал бота /api/bot/listings/*
            favorites.router,
            filters.router,
            notifications.router,
            calc.router,
        ],
    )


app = create_app()


