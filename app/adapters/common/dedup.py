"""Дедупликация (ТЗ п. 6.10).

- Внутри источника: уникальный индекс `(source, source_id)` в БД.
- Между источниками: ключ по VIN или fuzzy-хэш, TTL 48 часов в Redis.
"""

import hashlib
from typing import Any

from redis.asyncio import Redis

from app.schemas.listing import UnifiedListing

DEDUP_TTL_SEC = 48 * 60 * 60  # 48 часов


def vin_key(vin: str) -> str:
    normalized = vin.strip().upper()
    return "dedup:vin:" + hashlib.sha256(normalized.encode()).hexdigest()


def fuzzy_key(listing: UnifiedListing) -> str:
    """Хэш по make+model+year+mileage+price — когда VIN отсутствует."""
    parts = "|".join(
        str(v).strip().lower() if v is not None else "-"
        for v in (
            listing.make,
            listing.model,
            listing.year,
            listing.mileage_km,
            int(listing.price) if listing.price is not None else None,
        )
    )
    return "dedup:fuzzy:" + hashlib.sha256(parts.encode()).hexdigest()


def _dedup_keys(listing: UnifiedListing) -> list[str]:
    keys = []
    if listing.vin:
        keys.append(vin_key(listing.vin))
    if listing.make and listing.model:
        keys.append(fuzzy_key(listing))
    return keys


async def is_duplicate(redis: Redis, listing: UnifiedListing) -> bool:
    """True, если объявление уже встречалось за TTL.

    Побочный эффект: помечает ключи как «виденные» (SET NX + TTL).
    """
    keys = _dedup_keys(listing)
    if not keys:
        return False
    for key in keys:
        if not await redis.set(key, 1, nx=True, ex=DEDUP_TTL_SEC):
            return True
    return False


def raw_payload(listing: UnifiedListing) -> dict[str, Any]:
    """Сырые данные для колонки `listings.raw_payload` (перепарсинг)."""
    return {
        "source": listing.source,
        "schema_version": listing.schema_version,
        "raw": listing.raw,
        "dedup_keys": _dedup_keys(listing),
    }
