"""Разовый бэкфилл отчётов осмотра encar (фикс vehicleId, окт 2026).

Чек/диагностика/страховая API живут под ВНУТРЕННИМ vehicleId, а не под
id объявления — до фикса все эти запросы отдавали 404 и отчёты в БД
пустые (marks/items/images). Скрипт для каждого encar-листинга делает
enrich по актуальному адаптеру и апсертит ТОЛЬКО inspection_reports —
листинги и остальные поля не трогаются.

Запуск: docker compose exec worker_fetch python /app/scripts/backfill_inspection.py [limit]
"""

import asyncio
import sys

from sqlalchemy import select

from app.domain.listings.unified import UnifiedListing
from app.infrastructure.db.models import Listing
from app.infrastructure.db.repositories.listings_writer import (
    SqlAlchemyListingsWriter,
)
from app.infrastructure.db.session import session_factory
from app.infrastructure.sources.common.http import HttpClient
from app.infrastructure.sources.encar import EncarApiAdapter

BATCH = 30


async def main() -> None:
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    adapter = EncarApiAdapter(
        source_name="encar", http_client=HttpClient(timeout=15)
    )
    try:
        async with session_factory() as session:
            rows = (
                (
                    await session.execute(
                        select(Listing.id, Listing.source_id).where(
                            Listing.source == "encar"
                        )
                    )
                )
                .all()
            )
        if limit:
            rows = rows[:limit]
        print(f"backfill: {len(rows)} encar listings", flush=True)

        written = 0
        for start in range(0, len(rows), BATCH):
            chunk = rows[start : start + BATCH]
            items = [
                UnifiedListing(
                    source="encar",
                    source_id=sid,
                    url=f"https://fem.encar.com/car/detail/{sid}",
                )
                for _, sid in chunk
            ]
            await adapter._enrich_from_details(items)
            reports = [
                (listing_id, item.inspection)
                for (listing_id, _sid), item in zip(chunk, items)
                if item.inspection
            ]
            if reports:
                async with session_factory() as session:
                    writer = SqlAlchemyListingsWriter(session)
                    await writer._upsert_inspections(reports)
                    await session.commit()
                written += len(reports)
            print(
                f"  {min(start + BATCH, len(rows))}/{len(rows)} "
                f"(reports written: {written})",
                flush=True,
            )
        print(f"done: {written} reports upserted", flush=True)
    finally:
        await adapter.close()


if __name__ == "__main__":
    asyncio.run(main())
