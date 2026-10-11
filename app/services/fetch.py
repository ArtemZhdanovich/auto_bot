"""Сохранение UnifiedListing в БД с дедупликацией (ТЗ п. 6.10, этап 2)."""

import logging

from redis.asyncio import Redis
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.common.dedup import is_duplicate, raw_payload
from app.db.models import Listing
from app.schemas.listing import UnifiedListing

logger = logging.getLogger(__name__)


def _to_row(item: UnifiedListing) -> dict:
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
    """Сохраняет объявления. Дедуп:
    - внутри источника — ON CONFLICT DO NOTHING по (source, source_id);
    - между источниками — Redis-ключи по VIN/fuzzy с TTL 48 часов.

    Возвращает статистику {"saved": N, "in_source_dup": N, "cross_dup": N}.
    """
    saved = in_source_dup = cross_dup = 0
    for item in items:
        if redis is not None and await is_duplicate(redis, item):
            cross_dup += 1
            continue
        stmt = (
            pg_insert(Listing)
            .values(**_to_row(item))
            .on_conflict_do_nothing(constraint="uq_listings_source_source_id")
        )
        result = await session.execute(stmt)
        if result.rowcount:
            saved += 1
        else:
            in_source_dup += 1

    await session.commit()
    stats = {"saved": saved, "in_source_dup": in_source_dup, "cross_dup": cross_dup}
    if any(stats.values()):
        logger.info("listings_saved: %s", stats)
    return stats
