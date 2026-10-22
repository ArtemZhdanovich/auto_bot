"""Зависимости FastAPI: текущий пользователь (аутентификация — через dishka).

`get_current_user` — единственная точка аутентификации. Слои:

- `AccessTokenVerifier` (порт `app.application.auth`) — проверка Bearer-JWT;
  реализация (`JwtAccessTokenVerifier`) знает секрет из `Settings`, транспорт —
  нет: секрет приходит в контейнер, а не через `get_settings()` в коде.
- `GetCurrentUser` (сценарий) — загрузка профиля и проверка активности;
  ошибки сценария транспорт маппит: `UserNotFound` → 401, `UserDeactivated` →
  403 (не 401, иначе клиент зациклит refresh).
- Транспорт делает только то, что умеет только он: парсинг заголовка
  (`Header`) и перевод ошибок в HTTP-коды.

Токен парсится **до** обращения к БД: мусорные запросы без валидного токена
не занимают соединения пула (отдельная зависимость `_get_token_user_id`).
Ошибки аутентификации клиенту — generic (`"Invalid or expired token"`),
без деталей `InvalidToken`; полный текст — в лог (audit trail).
"""

import logging
from typing import Annotated

from dishka.integrations.fastapi import FromDishka, inject
from fastapi import Depends, Header, HTTPException, Request, status

from app.application.auth.errors import InvalidToken, UserDeactivated, UserNotFound
from app.application.auth.use_cases import GetCurrentUser, VerifyAccessToken
from app.application.dto.users import CurrentUser

logger = logging.getLogger(__name__)

_MAX_TOKEN_LEN = 8192  # JWT access-токен ~ 200-400 байт; 8KB — с запасом


@inject
async def _get_token_user_id(
    request: Request,
    verify_token: FromDishka[VerifyAccessToken],
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> int:
    """Парсит Bearer JWT и возвращает `user_id`. Без обращения к БД.

    Отдельная зависимость — чтобы мусорные запросы без валидного
    токена не открывали соединение с БД (DoS-защита пула).

    Args:
        request: FastAPI Request (для audit-лога IP/path).
        verify_token: Сценарий разбора access-JWT (из Dishka-контейнера).
        authorization: Заголовок `Authorization`.

    Returns:
        `telegram_id` пользователя из `sub` JWT.

    Raises:
        HTTPException: 401 если заголовок отсутствует, не Bearer, токен
            невалиден или не является access-токеном.
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        logger.warning(
            "auth_rejected reason=missing_or_bad_scheme path=%s ip=%s",
            request.url.path,
            request.client.host if request.client else None,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = authorization.split(" ", 1)[1].strip()
    if not token or len(token) > _MAX_TOKEN_LEN:
        logger.warning(
            "auth_rejected reason=empty_or_too_long path=%s",
            request.url.path,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        return verify_token.execute(token)
    except InvalidToken as exc:
        # Полный текст — в лог; клиенту — generic (без утечки деталей).
        logger.warning(
            "auth_rejected reason=invalid_token detail=%s path=%s ip=%s",
            exc,
            request.url.path,
            request.client.host if request.client else None,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


@inject
async def get_current_user(
    get_user: FromDishka[GetCurrentUser],
    user_id: Annotated[int, Depends(_get_token_user_id)],
) -> CurrentUser:
    """Загружает активного пользователя по `user_id` из JWT (сценарий).

    Args:
        get_user: Сценарий `GetCurrentUser` (REQUEST-скоуп Dishka).
        user_id: `telegram_id` из access-токена.

    Returns:
        `CurrentUser` (транспорт дальше передаёт только `telegram_id`).

    Raises:
        HTTPException: 401 если пользователь не найден (токен на удалённого);
            403 если пользователь деактивирован.
    """
    try:
        return await get_user.execute(user_id)
    except UserNotFound:
        # Токен валиден, но пользователя нет (удалён / отозван).
        logger.warning("auth_rejected reason=user_not_found user_id=%s", user_id)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from None
    except UserDeactivated:
        # 403, а не 401: клиент не должен зацикливать refresh.
        logger.warning("auth_rejected reason=user_inactive user_id=%s", user_id)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User is deactivated",
        ) from None


CurrentUserDep = Annotated[CurrentUser, Depends(get_current_user)]