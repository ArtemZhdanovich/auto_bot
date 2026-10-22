"""Адаптеры безопасности для авторизации.

Здесь живут реализации портов из `app.application.auth.use_cases`:

- `RedisLoginCodeStore` — одноразовые коды входа (TTL, state_hash, контекст
  инициатора);
- `JwtTokenIssuer` — выпуск access/refresh и разбор refresh с проверкой `type`;
- `TelegramInitDataVerifier` — HMAC-проверка `Telegram.WebApp.initData`;
- `Pbkdf2PasswordHasher` — PBKDF2-HMAC-SHA256 для веб-паролей.

Модуль намеренно не знает про FastAPI: транспорт получает эти объекты через
`transport/http/deps/auth_use_cases.py`.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import secrets
import time

from redis.asyncio import Redis

from app.application.auth.errors import (
    BotNotConfigured,
    InvalidInitData,
    InvalidToken,
)
from app.application.identity.dto import (
    LOGIN_OK,
    LOGIN_PENDING,
    LoginCode,
    TokenPair,
    TelegramIdentity,
)
from app.config import Settings
from app.infrastructure.security.tokens import (
    InitDataError,
    TokenError,
    create_access_token,
    create_refresh_token,
    decode_access_token,
    decode_refresh_token,
    validate_init_data,
)

logger = logging.getLogger(__name__)

_LOGIN_KEY_PREFIX = "tglogin:"

# Границы PBKDF2-итераций при verify: защита от DoS через подмену хеша в БД.
_PBKDF2_ITER_MIN = 100_000
_PBKDF2_ITER_MAX = 1_000_000
_PBKDF2_ITERATIONS = 240_000  # для новых хешей (OWASP-2024 рекомендует 600k — отдельный PR)

# Dummy-хеш для выравнивания тайминга при отсутствии пользователя (user enumeration).
_DUMMY_HASH = (
    "pbkdf2_sha256$240000$"
    "00000000000000000000000000000000$"
    "0000000000000000000000000000000000000000000000000000000000000000"
)


# ---------------------------------------------------------------------------
# Одноразовые коды входа (Redis)
# ---------------------------------------------------------------------------


def _login_key(code: str) -> str:
    """Redis-ключ одноразового кода входа."""
    return f"{_LOGIN_KEY_PREFIX}{code}"


def _parse(code: str, raw: str) -> LoginCode | None:
    """Разобрать JSON записи; `None` — запись повреждена."""
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        logger.warning("telegram_login_corrupted_state code=%s", code)
        return None
    if not isinstance(data, dict):
        logger.warning("telegram_login_corrupted_state code=%s", code)
        return None
    return LoginCode(
        status=data.get("status") or LOGIN_PENDING,
        state_hash=data.get("state_hash") or "",
        initiator_ip=data.get("initiator_ip"),
        initiator_ua=data.get("initiator_ua"),
        created_at=data.get("created_at"),
        telegram_id=data.get("telegram_id"),
        username=data.get("username"),
        first_name=data.get("first_name"),
        last_name=data.get("last_name"),
        language_code=data.get("language_code"),
    )


class RedisLoginCodeStore:
    """Одноразовые коды входа: JSON-запись на код с TTL."""

    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def save_pending(
        self,
        code: str,
        *,
        state_hash: str,
        initiator_ip: str | None,
        initiator_ua: str,
        created_at: int,
        ttl_sec: int,
    ) -> None:
        """Сохранить новый код в статусе `pending`."""
        await self._redis.set(
            _login_key(code),
            json.dumps(
                {
                    "status": LOGIN_PENDING,
                    "state_hash": state_hash,
                    "initiator_ip": initiator_ip,
                    "initiator_ua": initiator_ua,
                    "created_at": created_at,
                }
            ),
            ex=ttl_sec,
        )

    async def load(self, code: str) -> LoginCode | None:
        """Прочитать запись; `None` — кода нет или он повреждён."""
        raw = await self._redis.get(_login_key(code))
        if raw is None:
            return None
        return _parse(code, raw)

    async def touch(self, code: str, ttl_sec: int) -> None:
        """Продлить TTL (sliding window, пока пользователь в боте)."""
        await self._redis.expire(_login_key(code), ttl_sec)

    async def claim(self, code: str) -> LoginCode | None:
        """Забрать запись: `delete` вернёт 0, если параллельный запрос успел первым."""
        raw = await self._redis.get(_login_key(code))
        if raw is None:
            return None
        deleted = await self._redis.delete(_login_key(code))
        if not deleted:
            return None
        return _parse(code, raw)

    async def confirm(
        self,
        code: str,
        *,
        telegram_id: int,
        username: str | None,
        first_name: str | None,
        last_name: str | None,
        language_code: str | None,
        ttl_sec: int,
    ) -> LoginCode | None:
        """Пометить код подтверждённым; вернуть прежнюю запись или `None`.

        Идемпотентно: повторный confirm уже подтверждённого кода не переписывает
        запись (и не продлевает окно обмена) — возвращает её как есть.
        """
        key = _login_key(code)
        raw = await self._redis.get(key)
        if raw is None:
            return None
        record = _parse(code, raw)
        if record is None:
            return None
        if record.status == LOGIN_OK:
            return record

        await self._redis.set(
            key,
            json.dumps(
                {
                    "status": LOGIN_OK,
                    "state_hash": record.state_hash,
                    "initiator_ip": record.initiator_ip,
                    "initiator_ua": record.initiator_ua,
                    "created_at": record.created_at,
                    "confirmed_at": int(time.time()),
                    "telegram_id": telegram_id,
                    "username": username,
                    "first_name": first_name,
                    "last_name": last_name,
                    "language_code": language_code,
                }
            ),
            ex=ttl_sec,
        )
        return record

    async def delete(self, code: str) -> bool:
        """Удалить код; `False` — его уже нет."""
        return bool(await self._redis.delete(_login_key(code)))


# ---------------------------------------------------------------------------
# JWT
# ---------------------------------------------------------------------------


class JwtTokenIssuer:
    """JWT-пара для пользователя и разбор refresh-токена."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def issue(self, user_id: int) -> TokenPair:
        """Выпустить access + refresh (TTL — из настроек)."""
        return TokenPair(
            access_token=create_access_token(user_id, self._settings),
            refresh_token=create_refresh_token(user_id, self._settings),
        )

    def refresh_subject(self, token: str) -> int:
        """Вернуть `sub` refresh-токена.

        `decode_refresh_token` проверяет подпись, срок и `type == "refresh"`:
        access-токен в поле refresh не принимается.

        Raises:
            InvalidToken: Токен невалиден или не является refresh.
        """
        try:
            return decode_refresh_token(token, self._settings)
        except TokenError as exc:
            raise InvalidToken(str(exc)) from exc


class JwtAccessTokenVerifier:
    """Проверка access-JWT из `Authorization` (порт `AccessTokenVerifier`)."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def verify(self, token: str) -> int:
        """Вернуть `telegram_id` из валидного access-токена.

        `decode_access_token` проверяет подпись, срок и `type == "access"`:
        refresh-токен в заголовке авторизации не принимается.

        Raises:
            InvalidToken: Токен невалиден или не является access.
        """
        try:
            return decode_access_token(token, self._settings)
        except TokenError as exc:
            raise InvalidToken(str(exc)) from exc


# ---------------------------------------------------------------------------
# initData Mini App
# ---------------------------------------------------------------------------


class TelegramInitDataVerifier:
    """HMAC-SHA256 проверка initData по токену бота."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def verify(self, init_data: str) -> TelegramIdentity:
        """Проверить подпись и срок, вернуть профиль.

        `SecretStr` разворачивается явно: пустой `SecretStr` truthy, а
        `validate_init_data` падает на `.encode()` при передаче объекта.

        Raises:
            BotNotConfigured: BOT_TOKEN пуст (503).
            InvalidInitData: Подпись/срок/структура не в порядке (401).
        """
        token = (
            self._settings.bot_token.get_secret_value()
            if self._settings.bot_token
            else ""
        )
        if not token:
            raise BotNotConfigured

        try:
            tg_user = validate_init_data(
                init_data=init_data,
                bot_token=token,
                max_age_seconds=self._settings.init_data_max_age_sec,
            )
            telegram_id = int(tg_user["id"])
        except InitDataError as exc:
            logger.warning("initData login rejected: %s", exc)
            raise InvalidInitData(str(exc)) from exc
        except (KeyError, TypeError, ValueError) as exc:
            logger.warning("initData payload has no valid id: %r", tg_user)
            raise InvalidInitData("Invalid initData") from exc

        return TelegramIdentity(
            telegram_id=telegram_id,
            username=tg_user.get("username"),
            first_name=tg_user.get("first_name"),
            last_name=tg_user.get("last_name"),
            language_code=tg_user.get("language_code"),
        )


# ---------------------------------------------------------------------------
# PBKDF2
# ---------------------------------------------------------------------------


def _hash_password(password: str) -> str:
    """PBKDF2-HMAC-SHA256 с солью: `pbkdf2_sha256$iters$salt$digest`.

    Блокирующий вызов (~100ms на 240k итераций) — вызывается через
    `asyncio.to_thread`.
    """
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), salt.encode(), _PBKDF2_ITERATIONS
    ).hex()
    return f"pbkdf2_sha256${_PBKDF2_ITERATIONS}${salt}${digest}"


def _verify_password(password: str, stored: str) -> bool:
    """Проверить пароль против сохранённого хеша.

    Число итераций ограничено: подменённый хеш с `iterations=10^9` иначе
    повесит login-эндпоинт (DoS). Любое несовпадение формата — `False`.
    """
    try:
        algo, iterations_str, salt, digest = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        iterations = int(iterations_str)
        if not (_PBKDF2_ITER_MIN <= iterations <= _PBKDF2_ITER_MAX):
            return False
        candidate = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), salt.encode(), iterations
        ).hex()
        return hmac.compare_digest(candidate, digest)
    except (ValueError, TypeError):
        return False


class Pbkdf2PasswordHasher:
    """PBKDF2 для веб-паролей; CPU-работа уходит в `to_thread`."""

    async def hash(self, password: str) -> str:
        """Посчитать хеш нового пароля."""
        return await asyncio.to_thread(_hash_password, password)

    async def verify(self, password: str, stored_hash: str | None) -> bool:
        """Сверить пароль; при `None` считаем dummy-хеш и отвечаем `False`.

        PBKDF2 выполняется в обоих случаях — тайминг 401 не зависит от того,
        существует ли email и есть ли у аккаунта пароль.
        """
        valid = await asyncio.to_thread(
            _verify_password, password, stored_hash or _DUMMY_HASH
        )
        return valid and stored_hash is not None
