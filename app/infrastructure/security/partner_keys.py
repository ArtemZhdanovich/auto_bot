"""Формат, генерация, хеширование и маскирование партнёрских API-ключей.

Единое место знания о формате ключа: префикс `KEY_PREFIX` используют и
генерация (`generate_api_key`), и любые проверки формы ключа.
"""

import hashlib
import secrets

KEY_PREFIX = "pi_"


def generate_api_key() -> str:
    """`pi_` + 32 base64url-символа (192 бита энтропии)."""
    return KEY_PREFIX + secrets.token_urlsafe(24)


def hash_api_key(raw_key: str) -> str:
    """SHA-256 hex от raw-ключа (в БД хранится только хеш)."""
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def key_prefix(raw_key: str) -> str:
    """Первые 10 символов + '…' для отображения в ЛК/audit."""
    return raw_key[:10] + "…"
