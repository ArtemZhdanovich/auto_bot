"""Партнёрская аутентификация (`X-API-Key`) и rate-limit: тонкий transport-слой.

Сборка — в Dishka-контейнере: сценарий `AuthenticateApiKey` и порт
`PartnerRateLimiter` приходят через `FromDishka` (правило «ключ active И
аккаунт active» — в `SqlAlchemyPartnerRepository`, окно запросов —
`RedisRateLimiter`). Здесь только HTTP-маппинг:

- нет заголовка → 401; сценарий вернул `None` (не найден / отозван /
  suspended / чужой формат) → тот же 401 «Invalid API key»;
- превышение лимита → 429 (`PartnerRateLimiter.check` → `False`).
"""

from __future__ import annotations

from typing import Annotated

from dishka.integrations.fastapi import FromDishka, inject
from fastapi import Depends, Header, HTTPException, status

from app.application.partners.queries import (
    AuthenticateApiKey,
    PartnerAuthState,
    PartnerRateLimiter,
)


@inject
async def get_partner_auth(
    auth: FromDishka[AuthenticateApiKey],
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> PartnerAuthState:
    """FastAPI-зависимость: снимок аутентификации из `X-API-Key` (иначе 401)."""
    if not x_api_key:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing X-API-Key")
    state = await auth.execute(x_api_key)
    if state is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid API key")
    return state


PartnerAuthDep = Annotated[PartnerAuthState, Depends(get_partner_auth)]

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

