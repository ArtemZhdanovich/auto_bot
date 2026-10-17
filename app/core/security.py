"""Безопасность: валидация Telegram initData и JWT (ТЗ п. 4.3, 9.5)."""

import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import unquote

import jwt

from app.core.config import Settings


class InitDataError(ValueError):
    """Невалидный Telegram initData."""


class TokenError(ValueError):
    """Невалидный JWT."""


def validate_init_data(
    init_data: str,
    bot_token: str,
    max_age_seconds: int,
) -> dict[str, Any]:
    """Валидация initData из Mini App.

    Алгоритм: https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
    Возвращает данные пользователя (payload поля `user`).

    Важно: data_check_string собирается из сырых (URL-encoded) значений,
    как их прислал Telegram — расшифровка значений делается только после
    проверки подписи.
    """
    received_hash: str | None = None
    raw: dict[str, str] = {}
    for chunk in init_data.split("&"):
        if not chunk:
            continue
        key, _, value = chunk.partition("=")
        if key == "hash":
            received_hash = value
        else:
            raw[key] = value

    if not received_hash:
        raise InitDataError("initData: hash is missing")

    data_check_string = "\n".join(f"{key}={raw[key]}" for key in sorted(raw))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()

    def _hmac(data: str) -> str:
        return hmac.new(secret_key, data.encode(), hashlib.sha256).hexdigest()

    # Канон (дока Telegram): подпись по URL-encoded значениям как прислал клиент.
    # Наблюдение (Telegram Desktop 2026): часть клиентов подписывает initData по
    # РАСКОДИРОВАННЫМ значениям — пробуем оба варианта, иначе 401 «hash mismatch».
    computed_hash = _hmac(data_check_string)
    if not hmac.compare_digest(computed_hash, received_hash):
        decoded = "\n".join(f"{key}={unquote(raw[key])}" for key in sorted(raw))
        computed_hash = _hmac(decoded)
        if not hmac.compare_digest(computed_hash, received_hash):
            raise InitDataError("initData: hash mismatch")

    auth_date = int(raw.get("auth_date", "0"))
    auth_dt = datetime.fromtimestamp(auth_date, tz=UTC)
    if datetime.now(UTC) - auth_dt > timedelta(seconds=max_age_seconds):
        raise InitDataError("initData: expired")

    user_raw = unquote(raw.get("user", ""))
    user = json.loads(user_raw) if user_raw else None
    if not user or "id" not in user:
        raise InitDataError("initData: user is missing")
    return user


def _create_token(
    user_id: int,
    token_type: str,
    ttl_sec: int,
    settings: Settings,
) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "type": token_type,
        "iat": now,
        "exp": now + timedelta(seconds=ttl_sec),
    }
    return jwt.encode(payload, settings.app_secret_key, algorithm=settings.jwt_algorithm)


def create_access_token(user_id: int, settings: Settings) -> str:
    return _create_token(user_id, "access", settings.access_token_ttl_sec, settings)


def create_refresh_token(user_id: int, settings: Settings) -> str:
    return _create_token(user_id, "refresh", settings.refresh_token_ttl_sec, settings)


def decode_token(token: str, settings: Settings) -> dict[str, Any]:
    try:
        return jwt.decode(token, settings.app_secret_key, algorithms=[settings.jwt_algorithm])
    except jwt.PyJWTError as exc:
        raise TokenError("Invalid token") from exc


def decode_access_token(token: str, settings: Settings) -> int:
    """Декодирует access-токен и возвращает telegram_id пользователя."""
    payload = decode_token(token, settings)
    if payload.get("type") != "access":
        raise TokenError("Not an access token")
    return int(payload["sub"])
