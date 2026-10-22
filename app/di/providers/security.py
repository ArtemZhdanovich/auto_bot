"""Провайдеры безопасности: порты auth → JWT/код-сторы/верификатор.

Сценарии `app.application.auth` инжектят `TokenIssuer`, `LoginCodeStore`,
`InitDataVerifier`, `PasswordHasher`; конкретные классы знает только
контейнер. Всё долгоживущее (клиент Redis, настройки) — APP-скоуп.
"""

from __future__ import annotations

from dishka import Provider, Scope, provide
from redis.asyncio import Redis

from app.application.identity.ports import (
    AccessTokenVerifier,
    InitDataVerifier,
    LoginCodeStore,
    PasswordHasher,
    TokenIssuer,
)
from app.config import Settings
from app.infrastructure.security.login_codes import (
    JwtAccessTokenVerifier,
    JwtTokenIssuer,
    Pbkdf2PasswordHasher,
    RedisLoginCodeStore,
    TelegramInitDataVerifier,
)


class SecurityProvider(Provider):
    """Порты аутентификации в APP-скоупе (stateless или на общем Redis)."""

    scope = Scope.APP

    @provide
    def token_issuer(self, settings: Settings) -> TokenIssuer:
        """`TokenIssuer` → `JwtTokenIssuer` на настройках приложения.

        Args:
            settings: Настройки (секрет и TTL токенов).

        Returns:
            Эмитент access/refresh токенов.
        """
        return JwtTokenIssuer(settings)

    @provide
    def access_token_verifier(self, settings: Settings) -> AccessTokenVerifier:
        """`AccessTokenVerifier` → `JwtAccessTokenVerifier` (Bearer-проверка).

        Args:
            settings: Настройки (секрет подписи JWT).

        Returns:
            Верификатор access-токена для transport-аутентификации.
        """
        return JwtAccessTokenVerifier(settings)

    @provide
    def init_data_verifier(self, settings: Settings) -> InitDataVerifier:
        """`InitDataVerifier` → `TelegramInitDataVerifier`.

        Args:
            settings: Настройки (токен бота для HMAC-проверки).

        Returns:
            Верификатор `initData` Mini App.
        """
        return TelegramInitDataVerifier(settings)

    @provide
    def password_hasher(self) -> PasswordHasher:
        """`PasswordHasher` → `Pbkdf2PasswordHasher`.

        Returns:
            Хешер паролей веб-входа.
        """
        return Pbkdf2PasswordHasher()

    @provide
    def login_code_store(self, redis: Redis) -> LoginCodeStore:
        """`LoginCodeStore` → `RedisLoginCodeStore`.

        Args:
            redis: Клиент Redis приложения.

        Returns:
            Хранилище одноразовых кодов входа.
        """
        return RedisLoginCodeStore(redis)
