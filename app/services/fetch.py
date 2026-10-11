"""Сохранение UnifiedListing в БД: дедуп + отслеживание (PLAN.md, фазы 1–2).

Дедуп (порядок важен):
- между источниками — Redis-ключи по VIN/fuzzy (TTL 48 ч), проверяется ДО БД;
- внутри источника — ON CONFLICT (source, source_id) DO UPDATE (CTE, 1 запрос).

Отслеживание: content_hash, first/last_seen_at, is_active/disappeared_at,
точки цены в listing_price_history. Гонка конкурентных NEW (у проигравшего
old_* = NULL) для MVP допустима — см. PLAN.md, раздел 3.
"""

import asyncio
import hashlib
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import bindparam, func, select, text, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.common.dedup import is_duplicate, raw_payload
from app.core.config import get_settings
from app.db.models import Listing, SourceHealth
from app.schemas.listing import UnifiedListing
from app.services.storage import S3Storage, build_raw_key

logger = logging.getLogger(__name__)

# Upsert одним запросом: CTE `existing` (FOR UPDATE) даёт значения ДО
# обновления, RETURNING — после; LEFT JOIN возвращает строку и для NEW.
# - first_seen_at НЕ входит в DO UPDATE SET — не сбрасывается при парсинге;
# - disappeared_at = NULL безусловно — реактивация возвращённого объявления;
# - raw_payload в БД больше не пишется (фаза 6): ключ S3-объекта ставит
#   отдельный UPDATE после загрузки (raw_key = NULL, если MinIO недоступен).
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
        mileage_km, vin, images, schema_version, parsed_at,
        content_hash, is_active, first_seen_at, last_seen_at, title_raw
    ) VALUES (
        :source, :source_id, :url, :make, :model, :year, :price, :currency,
        :mileage_km, :vin, :images, :schema_version, :parsed_at,
        :content_hash, TRUE, now(), now(), :title_raw
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
        schema_version = EXCLUDED.schema_version,
        parsed_at = EXCLUDED.parsed_at,
        content_hash = EXCLUDED.content_hash,
        is_active = TRUE,
        disappeared_at = NULL,
        last_seen_at = EXCLUDED.last_seen_at,
        title_raw = EXCLUDED.title_raw,
        updated_at = now()
    RETURNING id
)
SELECT e.id AS old_id, e.price AS old_price, e.currency AS old_currency,
       e.content_hash AS old_hash, e.is_active AS was_active, u.id AS listing_id
FROM upserted u LEFT JOIN existing e ON TRUE
""").bindparams(
    bindparam("images", type_=JSONB),
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
    """Параметры VALUES для upsert (images идут через JSONB).

    Сырой payload в БД не пишется (фаза 6): уходит в S3 (storage),
    в listings — только ключ объекта (raw_key, отдельным UPDATE).
    """
    return {
        "source": item.source,
        "source_id": item.source_id,
        "url": str(item.url),
        "title_raw": item.title,
        "make": item.make,
        "model": item.model,
        "year": item.year,
        "price": item.price,
        "currency": item.currency,
        "mileage_km": item.mileage_km,
        "vin": item.vin,
        "images": [str(i) for i in item.images],
        "schema_version": item.schema_version,
        "parsed_at": item.parsed_at,
    }


async def _upload_raw(
    storage: S3Storage | None,
    uploads: list[tuple[int, str, UnifiedListing]],
) -> dict[int, str]:
    """Загрузка raw_payload в S3 параллельно; возвращает успешные ключи.

    Ошибка отдельной загрузки (put_raw возвращает False / бросает) не
    влияет на остальные и на сохранение объявлений: raw_key просто
    останется NULL (fallback, фаза 6).
    """
    if storage is None or not storage.enabled or not uploads:
        return {}
    keys = [
        build_raw_key(item.source, listing_id, content_hash, when=item.parsed_at)
        for listing_id, content_hash, item in uploads
    ]
    results = await asyncio.gather(
        *(
            storage.put_raw(
                key,
                raw_payload(item),
                {
                    "source": item.source,
                    "listing_id": listing_id,
                    "content_hash": content_hash,
                    "fetched_at": item.parsed_at,
                },
            )
            for (listing_id, content_hash, item), key in zip(uploads, keys)
        ),
        return_exceptions=True,
    )
    return {
        listing_id: key
        for (listing_id, _content_hash, _item), key, ok in zip(uploads, keys, results)
        if ok is True
    }


async def save_listings(
    session: AsyncSession,
    redis: Redis | None,
    items: list[UnifiedListing],
    storage: Any | None = None,
) -> dict[str, int]:
    """Сохраняет объявления с отслеживанием изменений (PLAN.md, фазы 1–2).

    Cross-dup по Redis проверяется ДО записи в БД. Дальше — CTE-upsert
    одним запросом (старые + новые значения сразу); точка цены пишется
    в listing_price_history при изменении пары (price, currency), для NEW —
    первая точка.

    storage (S3Storage, фаза 6) — raw_payload NEW/изменившихся объявлений
    уходит в MinIO; в listings пишется только ключ (raw_key). Хэш не
    изменился → объект не перезаписываем. Ошибка S3 → raw_key NULL,
    сохранение объявлений не ломается.

    Возвращает счётчики {"new", "updated", "unchanged", "price_changed",
    "reactivated", "cross_dup"} + id-списки "price_changed_ids" /
    "reactivated_ids" (аудитория статусных уведомлений, фаза 3) и
    "translate_ids" — NEW + смена content_hash (триггер повторного
    перевода тайтла, фаза 5).
    """
    stats = {
        "new": 0,
        "updated": 0,
        "unchanged": 0,
        "price_changed": 0,
        "reactivated": 0,
        "cross_dup": 0,
    }
    price_changed_ids: list[int] = []
    reactivated_ids: list[int] = []
    translate_ids: list[int] = []
    # (listing_id, content_hash, item) для загрузки raw_payload в S3 (фаза 6)
    pending_uploads: list[tuple[int, str, UnifiedListing]] = []
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
            translate_ids.append(row["listing_id"])  # фаза 5: перевод тайтла
            pending_uploads.append(  # фаза 6: raw_payload → S3
                (row["listing_id"], params["content_hash"], item)
            )
            price_point = item.price is not None
        elif not row["was_active"]:  # вернулось в выдачу
            stats["reactivated"] += 1
            reactivated_ids.append(row["listing_id"])
            price_point = price_changed
        elif row["old_hash"] == params["content_hash"]:  # без изменений
            stats["unchanged"] += 1
            price_point = False
        else:  # контент изменился
            translate_ids.append(row["listing_id"])  # тайтл мог измениться
            pending_uploads.append(  # хэш другой → новый объект в S3
                (row["listing_id"], params["content_hash"], item)
            )
            if price_changed:
                stats["price_changed"] += 1
                price_changed_ids.append(row["listing_id"])
            else:
                stats["updated"] += 1
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

    raw_keys = await _upload_raw(storage, pending_uploads)
    if raw_keys:
        stmt = (
            update(Listing)
            .where(Listing.id == bindparam("b_id"))
            .values(raw_key=bindparam("b_key"))
        )
        await session.execute(
            stmt, [{"b_id": lid, "b_key": key} for lid, key in raw_keys.items()]
        )

    await session.commit()
    if any(stats.values()):
        logger.info("listings_saved: %s", stats)
    return stats | {
        "price_changed_ids": price_changed_ids,
        "reactivated_ids": reactivated_ids,
        "translate_ids": translate_ids,
    }


async def mark_stale(
    session: AsyncSession, *, max_age_days: int | None = None
) -> list[int]:
    """Пометить устаревшие объявления (PLAN.md, фаза 3).

    is_active=false + disappeared_at для активных строк с last_seen_at старше
    порога (STALE_MAX_AGE_DAYS, по умолчанию 3 дня). Источники в circuit
    breaker (error_streak >= SOURCE_CB_ERROR_STREAK) пропускаются: источник
    лежит, его объявления не могли видеться. Возвращает id деактивированных
    (для «gone»-уведомлений).
    """
    settings = get_settings()
    age = settings.stale_max_age_days if max_age_days is None else max_age_days
    threshold = datetime.now(timezone.utc) - timedelta(days=age)
    cb_sources = (
        (
            await session.execute(
                select(SourceHealth.source_name).where(
                    SourceHealth.error_streak >= settings.source_cb_error_streak
                )
            )
        )
        .scalars()
        .all()
    )
    stmt = update(Listing).where(
        Listing.is_active.is_(True),
        Listing.last_seen_at < threshold,
    )
    if cb_sources:
        stmt = stmt.where(Listing.source.not_in(list(cb_sources)))
    result = await session.execute(
        stmt.values(is_active=False, disappeared_at=func.now()).returning(Listing.id)
    )
    ids = list(result.scalars().all())
    await session.commit()
    if ids:
        logger.info("mark_stale_done: gone=%s", len(ids))
    return ids
