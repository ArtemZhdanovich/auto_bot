"""Партнёрская аутентификация (`X-API-Key`) и rate-limit: тонкий transport-слой.

Сборка — в Dishka-контейнере (`deps/providers.py::HttpDepsProvider`):
снимок `PartnerAuthState` — REQUEST-скоуп фабрика над сценарием
`AuthenticateApiKey` (правило «ключ active И аккаунт active» — в
`SqlAlchemyPartnerRepository`); окно запросов — порт `PartnerRateLimiter`
(`RedisRateLimiter`). Здесь только алиасы для роутеров и маппинг
превышения лимита в 429. FastAPI `Depends` не используется.
"""

from __future__ import annotations

from dishka.integrations.fastapi import FromDishka
from fastapi import HTTPException, status

from app.application.partners.queries import PartnerAuthState, PartnerRateLimiter

PartnerAuthDep = FromDishka[PartnerAuthState]

RateLimitDep = FromDishka[PartnerRateLimiter]


async def enforce_rate_limit(
    limiter: PartnerRateLimiter, partner_id: int
) -> None:
    """Маппинг превышения окна запросов в 429.

    Механика окна и лимит — в реализации порта (`RedisRateLimiter`);
    здесь транспорт решает только, каким кодом ответить.
    """
    if not await limiter.check(partner_id):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Rate limit exceeded: too many requests per minute",
        )

