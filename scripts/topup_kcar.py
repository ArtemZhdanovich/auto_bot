"""Добор данных kcar по маркам (прод-ревизия 2026-10-24).

Поисковый API kcar не имеет пагинации: один запрос без критериев
отдаёт ~20–100 строк дефолтного ранжирования. Чтобы набрать базу
плотнее, обходим kcar отдельными запросами по топ-маркам (search API
поддерживает wr_in_mnuftr_nm). Каждый fetch переведён (NLLB+глоссарий).

Запуск (внутри worker_fetch; локально тоже работает):
    python scripts/topup_kcar.py            # все марки, page_size=100
    python scripts/topup_kcar.py --enrich   # + обогащение спеков из detail
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.domain.search.criteria import SearchCriteria  # noqa: E402
from app.infrastructure.db.models import Listing  # noqa: E402
from app.infrastructure.db.session import session_factory  # noqa: E402
from app.infrastructure.services.fetch import enrich_listing_specs, save_listings  # noqa: E402
from app.infrastructure.services.translate_client import (  # noqa: E402
    translate_client_from_settings,
)
from app.infrastructure.sources.kcar import KCAR_MAKE_NAMES  # noqa: E402
from app.infrastructure.sources.registry import AdapterRegistry  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("topup_kcar")

# Латинские канонические марки для критериев (adapter сам мапит в хангыль).
MAKES = list(KCAR_MAKE_NAMES)


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--enrich", action="store_true", help="обогатить спеки после добора")
    args = parser.parse_args()

    settings = get_settings()
    from redis.asyncio import Redis

    redis = Redis.from_url(settings.redis_url)
    registry = AdapterRegistry(
        session_factory, translator=translate_client_from_settings(redis)
    )
    total_new = 0
    try:
        await registry.load()
        adapter = registry.get("kcar")
        for make in MAKES:
            try:
                result = await adapter.fetch(SearchCriteria(make=make))
            except Exception:  # noqa: BLE001 — марка упала — идём дальше
                logger.warning("kcar %s failed", make, exc_info=True)
                continue
            if not result.items:
                logger.info("kcar %s: 0 items", make)
                continue
            async with session_factory() as session:
                stats = await save_listings(session, redis, result.items)
            total_new += stats.get("new", 0)
            logger.info(
                "kcar %s: +%d items (new=%d, cross_dup=%d)",
                make, len(result.items), stats.get("new", 0), stats.get("cross_dup", 0),
            )
    finally:
        await registry.aclose()
        await redis.aclose()

    if args.enrich:
        async with session_factory() as session:
            rows = (
                (
                    await session.execute(
                        select(Listing).where(
                            Listing.source == "kcar",
                            Listing.specs_enriched_at.is_(None),
                        )
                    )
                )
                .scalars()
                .all()
            )
            for row in rows:
                await enrich_listing_specs(session, row)
            await session.commit()
        logger.info("enriched specs for %d kcar rows", len(rows))

    logger.info("DONE: new kcar listings = %d", total_new)


if __name__ == "__main__":
    asyncio.run(main())
