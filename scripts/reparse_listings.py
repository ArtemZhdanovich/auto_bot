"""Разовый пересбор базы объявлений (прод-ревизия 2026-10).

Проблема: часть данных в БД осталась с корейскими полями (make/model/
color у kcar — 62 из 63), у kcar нет VIN/привода, переводы неполные.
Чинить построчно дороже, чем перезалить: сносим ВСЕ листинги (каскадом
уходят favorites/price_history/inspection_reports/price_analyses) и
парсим encar + kcar заново — адаптеры теперь переводят корейские поля
на лету (NLLB + глоссарий), encar тянет чеки осмотра при enrichment.

После парсинга:
- бэкфилл чеков осмотра encar (inspection_reports) — логика скрипта
  backfill_inspection (вызывается отдельно, см. --with-inspection);
- батч-обогащение спеков kcar/encar из detail-API (VIN/привод/объём).

Запуск (внутри контейнера worker_fetch или локально с DATABASE_URL):
    python scripts/reparse_listings.py [--limit-per-source N] [--keep]
    python scripts/reparse_listings.py --with-inspection   # + чеки encar
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import delete, select  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.infrastructure.db.models import Listing  # noqa: E402
from app.infrastructure.db.session import session_factory  # noqa: E402
from app.infrastructure.services.fetch import enrich_listing_specs  # noqa: E402
from app.infrastructure.services.translate_client import (  # noqa: E402
    translate_client_from_settings,
)
from app.infrastructure.sources.registry import AdapterRegistry  # noqa: E402
from app.domain.search.criteria import SearchCriteria  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("reparse")


async def wipe_listings() -> int:
    """Снести все объявления (каскад: favorite/history/report/analysis)."""
    from sqlalchemy import func

    async with session_factory() as session:
        result = await session.execute(select(func.count()).select_from(Listing))
        before = int(result.scalar_one())
        await session.execute(delete(Listing))
        await session.commit()
    logger.info("wiped %d listings", before)
    return before


async def fetch_all_sources(limit_per_source: int) -> dict[str, int]:
    """Перепарсить все включённые источники (адаптеры с переводчиком)."""
    settings = get_settings()
    from redis.asyncio import Redis

    redis = Redis.from_url(settings.redis_url)
    counts: dict[str, int] = {}
    try:
        registry = AdapterRegistry(session_factory, translator=translate_client_from_settings(redis))
        await registry.load()
        from app.infrastructure.services.fetch import save_listings

        for name in ("encar", "kcar"):
            try:
                adapter = registry.get(name)
            except KeyError:
                logger.warning("source %s not loaded, skip", name)
                continue
            total = 0
            cursor: str | None = None
            pages = 0
            failures = 0
            while pages < 50 and total < limit_per_source:
                try:
                    result = await adapter.fetch(SearchCriteria(), cursor)
                except Exception:  # noqa: BLE001 — флап источника не валит весь пересбор
                    failures += 1
                    logger.warning("%s fetch failed (page %d, attempt %d)",
                                   name, pages + 1, failures, exc_info=True)
                    if failures >= 3:
                        logger.error("%s: 3 сбоя подряд — переходим дальше", name)
                        break
                    await asyncio.sleep(10 * failures)
                    continue
                failures = 0
                async with session_factory() as session:
                    stats = await save_listings(session, redis, result.items)
                total += len(result.items)
                pages += 1
                logger.info(
                    "%s page %d: +%d items (new=%d dup=%d), total=%d",
                    name, pages, len(result.items),
                    stats.get("new", 0), stats.get("cross_dup", 0), total,
                )
                if not result.has_more or not result.next_cursor or not result.items:
                    break
                cursor = result.next_cursor
            counts[name] = total
    finally:
        await registry.aclose()
        await redis.aclose()
    return counts


async def enrich_specs(source: str, limit: int = 1000) -> int:
    """Батч-обогащение спеков из detail-API (VIN/привод/объём/цвет)."""
    enriched = 0
    async with session_factory() as session:
        rows = (
            (
                await session.execute(
                    select(Listing)
                    .where(Listing.source == source)
                    .order_by(Listing.parsed_at.desc())
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
        for row in rows:
            if await enrich_listing_specs(session, row):
                enriched += 1
    logger.info("enriched %s specs: %d/%d", source, enriched, len(rows))
    return enriched


async def translate_leftovers() -> int:
    """Перевести оставшийся хангыль в make/model/color (страховка).

    Адаптеры переводят на лету, но если микросервис переводов лежал в
    момент парсинга — здесь вторая попытка для полей объявления.
    """
    from app.infrastructure.services.translate_client import TranslateClient
    from app.domain.translation.glossary import apply_glossary
    from app.infrastructure.sources.common.translate import HANGUL_RE

    translator: TranslateClient = translate_client_from_settings()
    fixed_total = 0
    async with session_factory() as session:
        rows = (
            (await session.execute(select(Listing).limit(2000))).scalars().all()
        )
        for row in rows:
            changed = False
            for field in ("make", "model", "generation", "color"):
                value = getattr(row, field, None)
                if not value or not HANGUL_RE.search(value):
                    continue
                try:
                    translated = await translator.translate(
                        [apply_glossary(value)[:500]], source_lang="ko", target_lang="ru"
                    )
                except Exception:  # noqa: BLE001 — перевод необязателен
                    continue
                if translated and translated[0]:
                    setattr(row, field, translated[0])
                    changed = True
            if changed:
                fixed_total += 1
        await session.commit()
    logger.info("translated leftover listings: %d", fixed_total)
    return fixed_total


async def backfill_inspections() -> int:
    """Чеки осмотра encar: вызов логики scripts/backfill_inspection.py."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "backfill_inspection",
        str(Path(__file__).resolve().parent / "backfill_inspection.py"),
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    await module.main()
    return -1  # счёт внутри main; лог покажет детали


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit-per-source", type=int, default=300)
    parser.add_argument("--keep", action="store_true", help="не сносить базу")
    parser.add_argument(
        "--with-inspection", action="store_true", help="бэкфилл чеков encar после парсинга"
    )
    args = parser.parse_args()

    if not args.keep:
        await wipe_listings()
    counts = await fetch_all_sources(args.limit_per_source)
    logger.info("refetched: %s", counts)
    for name in counts:
        await enrich_specs(name)
    await translate_leftovers()
    if args.with_inspection:
        await backfill_inspections()
    logger.info("DONE")


if __name__ == "__main__":
    asyncio.run(main())
