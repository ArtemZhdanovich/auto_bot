"""Аутентификация HTTP-запросов как dishka-провайдеры (`Depends` выпилен).

`setup_dishka` ставит `ContainerMiddleware`, который кладёт Starlette
`Request` в контекст контейнера; `FastapiProvider` открывает его
провайдерам (`from_context`). Поэтому аутентификация — обычные
REQUEST-скоуп фабрики, а не FastAPI-зависимости:

- `CurrentUser` — Bearer JWT → порт `AccessTokenVerifier` (секрет из
  `Settings` в контейнере) → сценарий `GetCurrentUser`; HTTP-маппинг
  ошибок — здесь, транспортный слой;
- `PartnerAuthState` — `X-API-Key` → сценарий `AuthenticateApiKey`.

Роутеры получают готовые объекты через алиасы `CurrentUserDep` /
`PartnerAuthDep` (`FromDishka[...]`) при `route_class=DishkaRoute`.

Токен проверяется до обращения к БД: мусорные запросы без валидного
токена не занимают соединения пула. Ошибки клиенту — generic
(`"Invalid or expired token"`), без деталей `InvalidToken`;
полный текст — в лог (audit trail).
"""

from __future__ import annotations

import logging

from dishka import Scope, provide
from dishka.integrations.fastapi import FastapiProvider
from fastapi import HTTPException, Request, status

from app.application.auth.errors import InvalidToken, UserDeactivated, UserNotFound
from app.application.identity.ports import AccessTokenVerifier
from app.application.auth.use_cases import GetCurrentUser
from app.application.identity.dto import CurrentUser
from app.application.partners.queries import AuthenticateApiKey
from app.application.partners.dto import PartnerAuthState

logger = logging.getLogger(__name__)

_MAX_TOKEN_LEN = 8192  # JWT access-токен ~ 200-400 байт; 8KB — с запасом

_BEARER_CHALLENGE = {"WWW-Authenticate": "Bearer"}


class HttpDepsProvider(FastapiProvider):
    """HTTP-аутентификация как dishka-зависимости (REQUEST-скоуп)."""

    scope = Scope.REQUEST

    @provide
    async def current_user(
        self,
        request: Request,
        verify_token: AccessTokenVerifier,
        get_user: GetCurrentUser,
    ) -> CurrentUser:
        """Bearer JWT → профиль активного пользователя.

        Args:
            request: HTTP-запрос (из контекста dishka, `from_context`).
            verify_token: Порт проверки access-JWT (APP-скоуп).
            get_user: Сценарий загрузки профиля (REQUEST-скоуп).

        Returns:
            `CurrentUser` (транспорт дальше передаёт только `telegram_id`).

        Raises:
            HTTPException: 401 без валидного Bearer или если пользователь
                не найден (токен на удалённого); 403 если деактивирован
                (не 401, иначе клиент зациклит refresh).
        """
        authorization = request.headers.get("Authorization")
        if not authorization or not authorization.lower().startswith("bearer "):
            logger.warning(
                "auth_rejected reason=missing_or_bad_scheme path=%s ip=%s",
                request.url.path,
                request.client.host if request.client else None,
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing bearer token",
                headers=_BEARER_CHALLENGE,
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
                headers=_BEARER_CHALLENGE,
            )

        try:
            user_id = verify_token.verify(token)
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
                headers=_BEARER_CHALLENGE,
            ) from exc

        try:
            return await get_user.execute(user_id)
        except UserNotFound:
            # Токен валиден, но пользователя нет (удалён / отозван).
            logger.warning("auth_rejected reason=user_not_found user_id=%s", user_id)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired token",
                headers=_BEARER_CHALLENGE,
            ) from None
        except UserDeactivated:
            # 403, а не 401: клиент не должен зацикливать refresh.
            logger.warning("auth_rejected reason=user_inactive user_id=%s", user_id)
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="User is deactivated",
            ) from None

    @provide
    async def partner_auth(
        self,
        request: Request,
        auth: AuthenticateApiKey,
    ) -> PartnerAuthState:
        """`X-API-Key` → снимок партнёрской аутентификации (иначе 401).

        Args:
            request: HTTP-запрос (из контекста dishka).
            auth: Сценарий проверки ключа (REQUEST-скоуп).

        Returns:
            Снимок `PartnerAuthState` (ключ и аккаунт активны).

        Raises:
            HTTPException: 401 если заголовка нет или ключ не найден /
                отозван / suspended.
        """
        x_api_key = request.headers.get("X-API-Key")
        if not x_api_key:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing X-API-Key")
        state = await auth.execute(x_api_key)
        if state is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid API key")
        return state
