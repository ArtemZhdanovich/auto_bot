"""Зависимости партнёрского API: X-API-Key + rate limit (Redis, fixed window)."""

import logging
import time
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.deps_internal import get_redis
from app.db.models import PartnerAccount, PartnerApiKey
from app.db.session import get_session
from app.services import partners

PartnerSessionDep = Annotated[AsyncSession, Depends(get_session)]
RedisDep = Annotated[object, Depends(get_redis)]


async def check_rate_limit(redis: object, key_id: int) -> None:
    """Fixed window: не больше partner_rate_limit_per_min запросов в минуту.

    Redis недоступен → не блокируем партнёра (fail-open), только лог.
    """
    limit = get_settings().partner_rate_limit_per_min
    window = int(time.time() // 60)
    rkey = f"prl:{key_id}:{window}"
    try:
        count = await redis.incr(rkey)
        if count == 1:
            await redis.expire(rkey, 60)
        if count > limit:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Rate limit exceeded",
            )
    except HTTPException:
        raise
    except Exception:  # noqa: BLE001 — rate limit не должен ломать API
        logging.getLogger(__name__).warning(
            "partner_rate_limit_unavailable", exc_info=True
        )


async def require_partner(
    session: PartnerSessionDep,
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> tuple[PartnerApiKey, PartnerAccount]:
    """Аутентификация партнёра по X-API-Key (active ключ + active аккаунт)."""
    if not x_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing API key"
        )
    auth = await partners.authenticate_key(session, x_api_key)
    if auth is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key"
        )
    return auth


PartnerAuthDep = Annotated[tuple[PartnerApiKey, PartnerAccount], Depends(require_partner)]
