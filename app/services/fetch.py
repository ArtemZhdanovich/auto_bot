"""Сохранение UnifiedListing в БД: дедуп + отслеживание (PLAN.md, фазы 1–2).

Дедуп (порядок важен):
- между источниками — Redis-ключи по VIN/fuzzy (TTL 48 ч), проверяется ДО БД;
- внутри источника — ON CONFLICT (source, source_id) DO UPDATE (CTE, 1 запрос).

Отслеживание: content_hash, first/last_seen_at, is_active/disappeared_at,
точки цены в listing_price_history. Гонка конкурентных NEW (у проигравшего
old_* = NULL) для MVP допустима — см. PLAN.md, раздел 3.
"""

import hashlib
import logging

from redis.asyncio import Redis
from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.common.dedup import is_duplicate, raw_payload
from app.schemas.listing import UnifiedListing

logger = logging.getLogger(__name__)

# Upsert одним запросом: CTE `existing` (FOR UPDATE) даёт значения ДО
# обновления, RETURNING — после; LEFT JOIN возвращает строку и для NEW.
# - first_seen_at НЕ входит в DO UPDATE SET — не сбрасывается при парсинге;
# - disappeared_at = NULL безусловно — реактивация возвращённого объявления.
_UPSERT_SQL = text("""
WITH existing AS (
    SELECT id, price, currency, content_hash, is_active
    FROM listings
    WHERE source = :source AND source_id = :source_id
    FOR UPDATE
),
upserted AS (
    INSERT INTO listings (
        source, source_id, url, make, model, year, price, currency,
        mileage_km, vin, images, raw_payload, schema_version, parsed_at,
        content_hash, is_active, first_seen_at, last_seen_at
    ) VALUES (
        :source, :source_id, :url, :make, :model, :year, :price, :currency,
        :mileage_km, :vin, :images, :raw_payload, :schema_version, :parsed_at,
        :content_hash, TRUE, now(), now()
    )
    ON CONFLICT ON CONSTRAINT uq_listings_source_source_id DO UPDATE SET
        url = EXCLUDED.url,
        make = EXCLUDED.make,
        model = EXCLUDED.model,
        year = EXCLUDED.year,
        price = EXCLUDED.price,
        currency = EXCLUDED.currency,
        mileage_km = EXCLUDED.mileage_km,
        vin = EXCLUDED.vin,
        images = EXCLUDED.images,
        raw_payload = EXCLUDED.raw_payload,
        schema_version = EXCLUDED.schema_version,
        parsed_at = EXCLUDED.parsed_at,
        content_hash = EXCLUDED.content_hash,
        is_active = TRUE,
        disappeared_at = NULL,
        last_seen_at = EXCLUDED.last_seen_at,
        updated_at = now()
    RETURNING id
)
SELECT e.id AS old_id, e.price AS old_price, e.currency AS old_currency,
       e.content_hash AS old_hash, e.is_active AS was_active, u.id AS listing_id
FROM upserted u LEFT JOIN existing e ON TRUE
""").bindparams(
    bindparam("images", type_=JSONB),
    bindparam("raw_payload", type_=JSONB),
)

_PRICE_POINT_SQL = text(
    "INSERT INTO listing_price_history (listing_id, price, currency) "
    "VALUES (:listing_id, :price, :currency)"
)


def compute_content_hash(item: UnifiedListing) -> str:
    """sha256(title|price|currency|mileage_km|sorted(images)).

    Без parsed_at/updated_at (меняются при каждом парсинге) и без description
    (поля нет в контракте). Смена хэша = «объявление изменилось»; в фазе 5
    этот же хэш — триггер повторного перевода (PLAN.md, раздел 1).
    """
    payload = "|".join(
        [
            item.title or "",
            str(item.price or ""),
            item.currency or "",
            str(item.mileage_km or ""),
            ",".join(sorted(str(u) for u in item.images)),
        ]
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _to_row(item: UnifiedListing) -> dict:
    """Параметры VALUES для upsert (images/raw_payload идут через JSONB)."""
    return {
        "source": item.source,
        "source_id": item.source_id,
        "url": str(item.url),
        "make": item.make,
        "model": item.model,
        "year": item.year,
        "price": item.price,
        "currency": item.currency,
        "mileage_km": item.mileage_km,
        "vin": item.vin,
        "images": [str(i) for i in item.images],
        "raw_payload": raw_payload(item),
        "schema_version": item.schema_version,
        "parsed_at": item.parsed_at,
    }


async def save_listings(
    session: AsyncSession,
    redis: Redis | None,
    items: list[UnifiedListing],
) -> dict[str, int]:
    """Сохраняет объявления с отслеживанием изменений (PLAN.md, фазы 1–2).

    Cross-dup по Redis проверяется ДО записи в БД. Дальше — CTE-upsert
    одним запросом (старые + новые значения сразу); точка цены пишется
    в listing_price_history при изменении пары (price, currency), для NEW —
    первая точка.

    Возвращает {"new", "updated", "unchanged", "price_changed",
    "reactivated", "cross_dup"}.
    """
    stats = {
        "new": 0,
        "updated": 0,
        "unchanged": 0,
        "price_changed": 0,
        "reactivated": 0,
        "cross_dup": 0,
    }
    for item in items:
        if redis is not None and await is_duplicate(redis, item):
            stats["cross_dup"] += 1
            continue
        params = _to_row(item) | {"content_hash": compute_content_hash(item)}
        row = (await session.execute(_UPSERT_SQL, params)).mappings().first()
        if row is None:  # CTE всегда возвращает строку; защита от аномалий
            continue
        price_changed = item.price is not None and (
            row["old_price"] is None
            or item.price != row["old_price"]
            or item.currency != row["old_currency"]
        )
        if row["old_id"] is None:  # NEW — первая точка цены
            stats["new"] += 1
            price_point = item.price is not None
        elif not row["was_active"]:  # вернулось в выдачу
            stats["reactivated"] += 1
            price_point = price_changed
        elif row["old_hash"] == params["content_hash"]:  # без изменений
            stats["unchanged"] += 1
            price_point = False
        else:  # контент изменился
            stats["price_changed" if price_changed else "updated"] += 1
            price_point = price_changed
        if price_point:
            await session.execute(
                _PRICE_POINT_SQL,
                {
                    "listing_id": row["listing_id"],
                    "price": item.price,
                    "currency": item.currency,
                },
            )

    await session.commit()
    if any(stats.values()):
        logger.info("listings_saved: %s", stats)
    return stats
