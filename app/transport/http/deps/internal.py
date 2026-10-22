"""Вспомогательные зависимости для внутренних/admin эндпоинтов."""

import hmac
import logging

from dishka.integrations.fastapi import FromDishka
from fastapi import Header, HTTPException, status
from redis.asyncio import Redis

from app.config import get_settings

logger = logging.getLogger(__name__)


def _as_bytes(value: str) -> bytes:
    """str -> bytes без TypeError на non-ASCII.

    hmac.compare_digest для str падает с TypeError, если есть символы вне ASCII.
    Сравниваем байты — это ещё и constant-time для одинаковой длины.
    """
    return value.encode("utf-8", errors="surrogatepass")


def internal_token() -> str:
    """Секрет для внутренних эндпоинтов: INTERNAL_TOKEN, иначе APP_SECRET_KEY.

    Пустое значение — мисконфиг, а не «открытый доступ»: fail-closed 503.
    """
    settings = get_settings()
    # SecretStr("") — truthy объект, поэтому `or` по объектам не работает:
    # сравниваем уже развёрнутые строки, иначе пустой INTERNAL_TOKEN
    # блокирует fallback на APP_SECRET_KEY и уходит в compare_digest пустым.
    token = (
        settings.internal_token.get_secret_value()
        or settings.app_secret_key.get_secret_value()
    )
    if not token:
        logger.error("INTERNAL_TOKEN and APP_SECRET_KEY are both empty")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Internal API is not configured",
        )
    return token


def check_internal_token(
    x_internal_token: str | None = Header(default=None, alias="X-Internal-Token"),
) -> None:
    """Внутренние эндпоинты (бот/воркеры) — INTERNAL_TOKEN или APP_SECRET_KEY.

    Порядок:
        1) expected = internal_token()  -> 503 при мисконфиге
        2) пустой/отсутствующий заголовок -> 401 (не доходим до compare_digest)
        3) constant-time сравнение в байтах -> 401 при несовпадении
    """
    expected = internal_token()

    if not x_internal_token:
        logger.warning("Internal token missing or empty")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid internal token",
            headers={"WWW-Authenticate": "X-Internal-Token"},
        )

    try:
        valid = hmac.compare_digest(_as_bytes(x_internal_token), _as_bytes(expected))
    except (TypeError, AttributeError):
        # на всякий случай: compare_digest больше не должен падать, но пусть 401
        valid = False

    if not valid:
        logger.warning("Invalid internal token attempt")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid internal token",
            headers={"WWW-Authenticate": "X-Internal-Token"},
        )


# Redis для роутеров: клиент приходит из Dishka-контейнера (APP-скоуп,
# `app.di.providers.redis.RedisProvider` — он же валидирует REDIS_URL).
RedisDep = FromDishka[Redis]
