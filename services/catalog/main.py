"""catalog-service (8102): лента объявлений и пользовательские подборки.

Домены: /api/listings (лента, карточка, история цен), /api/favorites,
/api/filters (CRUD с тарифными лимитами), /api/notifications
(режим уведомлений и тихие часы — применяются к фильтрам).
"""

from fastapi import FastAPI

from app.transport.http.routers import favorites, filters, listings, notifications
from services._base import create_domain_service

SERVICE_NAME = "catalog"


def create_app() -> FastAPI:
    return create_domain_service(
        SERVICE_NAME,
        "0.1.0",
        [listings.router, favorites.router, filters.router, notifications.router],
    )


app = create_app()
