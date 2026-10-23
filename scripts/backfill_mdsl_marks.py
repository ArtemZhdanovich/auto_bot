"""Разовый бэкфилл: ранки панелей из классического чека (mdsl_regcar.do).

Карта повреждений «двух машинок» (외판 1/2, 주요골격 A/B/C) живёт ТОЛЬКО
на классической странице www.encar.com — JSON-чек (`outers`) у большинства
авто пуст. Скрипт для каждого encar-листинга с отчётом достаёт страницу
чека, мержит панели с существующими marks (зоны JSON-чека/диагностики
приоритетны, дублей нет) и обновляет ТОЛЬКО inspection_reports.marks.

Запуск: docker compose exec worker_fetch python /app/scripts/backfill_mdsl_marks.py [limit]
"""

import asyncio
import sys

from sqlalchemy import select

from app.infrastructure.db.models import InspectionReport, Listing
from app.infrastructure.db.session import session_factory
from app.infrastructure.sources.common.http import HttpClient
from app.infrastructure.sources.encar import _parse_mdsl_panels

BATCH = 30
_CONCURRENCY = 2


async def main() -> None:
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    http = HttpClient(timeout=30)
    semaphore = asyncio.Semaphore(_CONCURRENCY)
    try:
        async with session_factory() as session:
            rows = (
                (
                    await session.execute(
                        select(Listing.id, Listing.source_id)
                        .join(
                            InspectionReport,
                            InspectionReport.listing_id == Listing.id,
                        )
                        .where(Listing.source == "encar")
                    )
                )
                .all()
            )
        if limit:
            rows = rows[:limit]
        print(f"backfill_mdsl: {len(rows)} reports", flush=True)

        updated = 0

        async def fetch_one(listing_id: int, source_id: str) -> tuple[int, list] | None:
            async with semaphore:
                try:
                    resp = await http.get(
                        "https://www.encar.com/md/sl/mdsl_regcar.do"
                        f"?method=inspectionViewNew&carid={source_id}"
                    )
                except Exception as exc:
                    print(f"  ERR {source_id}: {exc!r:.80}", flush=True)
                    return None
                if resp.status_code != 200:
                    return None
                html = resp.content.decode("euc-kr", errors="replace")
                return listing_id, _parse_mdsl_panels(html)

        for start in range(0, len(rows), BATCH):
            chunk = rows[start : start + BATCH]
            results = await asyncio.gather(
                *(fetch_one(lid, sid) for lid, sid in chunk)
            )
            updates: list[tuple[int, list]] = []
            for res in results:
                if res is None:
                    continue
                listing_id, legacy = res
                if not legacy:
                    continue
                current = report_marks.get(listing_id, [])
                seen = {m.get("zone") for m in current}
                add = [m for m in legacy if m["zone"] not in seen]
                if add:
                    updates.append((listing_id, current + add))
            if updates:
                async with session_factory() as session:
                    for listing_id, marks in updates:
                        await session.execute(
                            InspectionReport.__table__.update()
                            .where(InspectionReport.listing_id == listing_id)
                            .values(marks=marks)
                        )
                    await session.commit()
                updated += len(updates)
            print(
                f"  {min(start + BATCH, len(rows))}/{len(rows)} "
                f"(updated: {updated})",
                flush=True,
            )
        print(f"done: {updated} reports updated", flush=True)
    finally:
        await http.close()


# zone → существующие marks всех отчётов (одним запросом, без ORM-лени)
report_marks: dict[int, list] = {}


async def _load_marks() -> None:
    async with session_factory() as session:
        rows = (
            (
                await session.execute(
                    select(InspectionReport.listing_id, InspectionReport.marks)
                )
            )
            .all()
        )
    for listing_id, marks in rows:
        report_marks[listing_id] = marks or []


async def _load_marks_then_main() -> None:
    await _load_marks()
    await main()


if __name__ == "__main__":
    asyncio.run(_load_marks_then_main())
