"""Вспомогательные зависимости для внутренних/admin эндпоинтов."""

from functools import lru_cache

from fastapi import Header, HTTPException, status
from redis.asyncio import Redis

from app.core.config import get_settings


def check_internal_token(
    x_internal_token: str | None = Header(default=None, alias="X-Internal-Token"),
) -> None:
    """Внутренние эндпоинты (бот/воркеры) — общий секрет APP_SECRET_KEY.

    Аннотация Header позволяет использовать и напрямую в Depends(router-level),
    и с явной передачей значения из эндпоинтов admin."""
    if x_internal_token != get_settings().app_secret_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid internal token"
        )


@lru_cache
def get_redis() -> Redis:
    """Общий клиент Redis для диалогов (bot/backend в одном процессе)."""
    return Redis.from_url(get_settings().redis_url)

