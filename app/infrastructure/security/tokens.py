"""Безопасность: валидация Telegram initData и JWT (ТЗ п. 4.3, 9.5).

Аутентификация:
- `validate_init_data` — HMAC-SHA256 по data_check_string (raw URL-encoded
  значения, как их прислал Telegram; см. официальные доки).
- `create_access_token` / `create_refresh_token` — HS256 JWT с `sub`, `type`,
  `iat`, `exp`.
- `decode_token` — проверка подписи/`exp`/`iat`/`sub`/`type` + leeway 10s.
- `decode_access_token` / `decode_refresh_token` — с проверкой типа.

Секреты (`Settings.app_secret_key`, `Settings.bot_token`) хранятся как
`SecretStr`; здесь они явно разворачиваются через `.get_secret_value()`.
Прямая передача `SecretStr` в `jwt.encode/decode` даёт либо `'**********'`
(pydantic v2 `__str__`), либо `TypeError` — токены подделаны или 500.
"""

import hashlib
import hmac
import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import unquote

import jwt
from pydantic import SecretStr

from app.config import Settings

logger = logging.getLogger(__name__)

# Допустимый clock skew между серверами (сек).
_JWT_LEEWAY_SEC = 10
# Допустимое «будущее» для auth_date initData (сек).
_AUTH_DATE_FUTURE_SKEW_SEC = 60
# Максимальная длина initData (защита от DoS на парсинге).
_MAX_INIT_DATA_LEN = 16 * 1024


class InitDataError(ValueError):
    """Невалидный Telegram initData."""


class TokenError(ValueError):
    """Невалидный JWT."""


def _secret(value: SecretStr | str) -> str:
    """Разворачивает `SecretStr` в `str`; `str` возвращает как есть.

    Args:
        value: `SecretStr` или `str`.

    Returns:
        Секрет в виде строки.

    Raises:
        TokenError: Если секрет пуст.
    """
    secret = value.get_secret_value() if isinstance(value, SecretStr) else value
    if not secret:
        raise TokenError("Secret is empty")
    return secret


def _is_hex(s: str) -> bool:
    """True, если `s` — hex-строка (0-9a-fA-F)."""
    return bool(s) and all(c in "0123456789abcdefABCDEF" for c in s)


# ---------------------------------------------------------------------------
# Telegram initData
# ---------------------------------------------------------------------------


def validate_init_data(
    init_data: str,
    bot_token: str | SecretStr,
    max_age_seconds: int,
) -> dict[str, Any]:
    """Валидация initData из Mini App.

    Алгоритм: https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app

    `data_check_string` собирается из **сырых** (URL-encoded) значений,
    как их прислал Telegram — расшифровка значений делается **только**
    после проверки подписи (спека Telegram).

    Args:
        init_data: Строка `Telegram.WebApp.initData`.
        bot_token: Токен бота (`SecretStr` или `str`).
        max_age_seconds: Максимальный возраст initData (сек).

    Returns:
        Словарь `user` из initData.

    Raises:
        InitDataError: Если initData невалиден (нет hash, истёк, подпись
            не совпадает, user отсутствует/невалиден).
    """
    if not init_data:
        raise InitDataError("initData: empty")
    if len(init_data) > _MAX_INIT_DATA_LEN:
        raise InitDataError("initData: too long")
    if max_age_seconds <= 0:
        raise InitDataError("initData: max_age_seconds must be > 0")

    received_hash: str | None = None
    raw: dict[str, str] = {}
    for chunk in init_data.split("&"):
        if not chunk:
            continue
        key, _, value = chunk.partition("=")
        if key == "hash":
            received_hash = value
        elif key in raw:
            # Дубликаты запрещены: разные парсеры выбирают разные значения,
            # а подпись покрывает только одно.
            raise InitDataError(f"initData: duplicate key {key!r}")
        else:
            raw[key] = value

    if not received_hash:
        raise InitDataError("initData: hash is missing")
    if not _is_hex(received_hash):
        raise InitDataError("initData: hash is not valid hex")

    token_str = bot_token.get_secret_value() if isinstance(bot_token, SecretStr) else bot_token
    if not token_str:
        raise InitDataError("initData: bot_token is empty")

    data_check_string = "\n".join(f"{key}={raw[key]}" for key in sorted(raw))
    secret_key = hmac.new(b"WebAppData", token_str.encode(), hashlib.sha256).digest()
    computed_hash = hmac.new(
        secret_key, data_check_string.encode(), hashlib.sha256
    ).hexdigest()

    # WORKAROUND: часть клиентов (Telegram Desktop 2026) подписывает
    # decoded-значения — отклонение от спеки. Принимаем **только** как
    # fallback, но данные читаем из того варианта, чья подпись совпала
    # (а не всегда из raw) — иначе рассинхрон «подпись по decoded,
    # данные из raw».
    used_data = raw
    if not hmac.compare_digest(computed_hash.lower(), received_hash.lower()):
        decoded = {k: unquote(v) for k, v in raw.items()}
        decoded_dcs = "\n".join(f"{key}={decoded[key]}" for key in sorted(decoded))
        decoded_hash = hmac.new(
            secret_key, decoded_dcs.encode(), hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(decoded_hash.lower(), received_hash.lower()):
            raise InitDataError("initData: hash mismatch")
        logger.warning(
            "initdata_decoded_variant_used "
            "(deviation from Telegram spec; see validate_init_data docstring)"
        )
        used_data = decoded

    auth_date_str = used_data.get("auth_date")
    if not auth_date_str:
        raise InitDataError("initData: auth_date is missing")
    try:
        auth_date = int(auth_date_str)
    except (TypeError, ValueError) as exc:
        raise InitDataError("initData: auth_date is not a number") from exc
    try:
        auth_dt = datetime.fromtimestamp(auth_date, tz=UTC)
    except (OSError, OverflowError, ValueError) as exc:
        raise InitDataError("initData: auth_date is out of range") from exc

    now = datetime.now(UTC)
    if now - auth_dt > timedelta(seconds=max_age_seconds):
        raise InitDataError("initData: expired")
    if auth_dt - now > timedelta(seconds=_AUTH_DATE_FUTURE_SKEW_SEC):
        raise InitDataError("initData: auth_date is in the future")

    user_raw = used_data.get("user", "")
    if not user_raw:
        raise InitDataError("initData: user is missing")
    try:
        user = json.loads(user_raw)
    except json.JSONDecodeError:
        # Канонический initData приходит с percent-encoded `user`
        # (Telegram Web/Desktop). Пробуем декодировать один раз.
        try:
            user = json.loads(unquote(user_raw))
        except json.JSONDecodeError as exc:
            raise InitDataError("initData: user is not valid JSON") from exc
    if not isinstance(user, dict) or "id" not in user:
        raise InitDataError("initData: user is missing id")
    return user


# ---------------------------------------------------------------------------
# JWT
# ---------------------------------------------------------------------------


def _create_token(
    user_id: int,
    token_type: str,
    ttl_sec: int,
    settings: Settings,
) -> str:
    if ttl_sec <= 0:
        raise TokenError(f"Invalid ttl_sec={ttl_sec}")
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "type": token_type,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl_sec)).timestamp()),
    }
    return jwt.encode(
        payload,
        _secret(settings.app_secret_key),
        algorithm=settings.jwt_algorithm,
    )


def create_access_token(user_id: int, settings: Settings) -> str:
    """Access-токен (TTL `settings.access_token_ttl_sec`)."""
    return _create_token(user_id, "access", settings.access_token_ttl_sec, settings)


def create_refresh_token(user_id: int, settings: Settings) -> str:
    """Refresh-токен (TTL `settings.refresh_token_ttl_sec`)."""
    return _create_token(user_id, "refresh", settings.refresh_token_ttl_sec, settings)


def decode_token(token: str, settings: Settings) -> dict[str, Any]:
    """Декодирует JWT без проверки типа. Не использовать снаружи.

    Проверяет подпись, `exp`, `iat`, наличие `sub`/`type`. Leeway 10s
    покрывает clock skew между серверами.

    Args:
        token: JWT.
        settings: Настройки.

    Returns:
        Payload.

    Raises:
        TokenError: Подпись/срок/структура невалидны.
    """
    try:
        return jwt.decode(
            token,
            _secret(settings.app_secret_key),
            algorithms=[settings.jwt_algorithm],
            options={"require": ["exp", "iat", "sub", "type"]},
            leeway=timedelta(seconds=_JWT_LEEWAY_SEC),
        )
    except jwt.PyJWTError as exc:
        # Детали (expired/signature) — в лог, наружу — generic TokenError.
        logger.warning("jwt_decode_failed reason=%s", type(exc).__name__)
        raise TokenError("Invalid token") from exc


def decode_access_token(token: str, settings: Settings) -> int:
    """Декодирует access-токен и возвращает telegram_id.

    Args:
        token: Access-JWT.
        settings: Настройки.

    Returns:
        `telegram_id`.

    Raises:
        TokenError: Не access-токен, невалидный `sub` или подпись.
    """
    payload = decode_token(token, settings)
    if payload.get("type") != "access":
        raise TokenError("Not an access token")
    sub = payload.get("sub")
    if not isinstance(sub, str) or not sub:
        raise TokenError("Invalid subject")
    try:
        return int(sub)
    except ValueError as exc:
        raise TokenError("Invalid subject") from exc


def decode_refresh_token(token: str, settings: Settings) -> int:
    """Декодирует refresh-токен и возвращает telegram_id.

    Args:
        token: Refresh-JWT.
        settings: Настройки.

    Returns:
        `telegram_id`.

    Raises:
        TokenError: Не refresh-токен, невалидный `sub` или подпись.
    """
    payload = decode_token(token, settings)
    if payload.get("type") != "refresh":
        raise TokenError("Not a refresh token")
    sub = payload.get("sub")
    if not isinstance(sub, str) or not sub:
        raise TokenError("Invalid subject")
    try:
        return int(sub)
    except ValueError as exc:
        raise TokenError("Invalid subject") from exc