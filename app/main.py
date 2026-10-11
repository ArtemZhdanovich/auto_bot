"""Точка входа Backend (FastAPI). Запуск: uvicorn app.main:app (ТЗ п. 4.3)."""

import logging

from fastapi import FastAPI

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

settings = get_settings()

logging.basicConfig(level=settings.log_level)

app = FastAPI(
    title="Auto Selection Ecosystem API",
    version="0.1.0",
    description="Backend для Telegram-бота и Mini App (ТЗ п. 4.3)",
)


@app.get("/health", tags=["system"])
async def health() -> dict[str, str]:
    return {"status": "ok", "env": settings.app_env}


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
