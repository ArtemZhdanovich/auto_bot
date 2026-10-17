"""Вспомогательные зависимости для внутренних/admin эндпоинтов."""

import hmac
from typing import Annotated
from functools import lru_cache

from fastapi import Depends, Header, HTTPException, status
from redis.asyncio import Redis

from app.core.config import get_settings


def internal_token() -> str:
    """Секрет для внутренних эндпоинтов: INTERNAL_TOKEN, иначе APP_SECRET_KEY.

    Отдельная переменная разграничивает JWT-подпись и доступ к
    internal/admin API; пустое значение сохраняет обратную совместимость.
    """
    settings = get_settings()
    return settings.internal_token or settings.app_secret_key


def check_internal_token(
    x_internal_token: str | None = Header(default=None, alias="X-Internal-Token"),
) -> None:
    """Внутренние эндпоинты (бот/воркеры) — INTERNAL_TOKEN или APP_SECRET_KEY.

    Аннотация Header позволяет использовать и напрямую в Depends(router-level),
    и с явной передачей значения из эндпоинтов admin.
    Сравнение в постоянном времени (hmac.compare_digest) — не раскрываем секрет
    через тайминги ответов.
    """
    if x_internal_token is None or not hmac.compare_digest(
        x_internal_token, internal_token()
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid internal token"
        )


@lru_cache
def get_redis() -> Redis:
    """Общий клиент Redis для диалогов (bot/backend в одном процессе)."""
    return Redis.from_url(get_settings().redis_url)


RedisDep = Annotated[Redis, Depends(get_redis)]

