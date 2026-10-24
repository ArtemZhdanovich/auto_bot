"""Разовый перевод корейских значений в БД на русский (окт 2026).

Encar отдаёт марку/модель/цвет/поколение и поля отчёта осмотра
(inspector/summary/notes) по-корейски; до фикса они писались в БД как есть.
Скрипт прогоняет существующие encar-строки через `korean.dehangul`
(карты слов + транслитерация) и апдейтит только затронутые строки.
Идемпотентно: строки без хангыля не меняются.

Запуск: docker compose exec worker_fetch python /app/scripts/translate_db_russian.py
"""

import json

from sqlalchemy import select

from app.infrastructure.db.models import InspectionReport, Listing
from app.infrastructure.db.session import session_factory
from app.infrastructure.sources.korean import (
    dehangul,
    has_hangul,
    ru_color,
    ru_make,
    ru_model,
)


async def main() -> None:
    listings_changed = reports_changed = 0
    async with session_factory() as session:
        rows = (
            await session.execute(select(Listing).where(Listing.source == "encar"))
        ).scalars().all()
        for row in rows:
            dirty = False
            if has_hangul(row.make):
                row.make = ru_make(row.make)
                dirty = True
            if has_hangul(row.model):
                row.model = ru_model(row.model)
                dirty = True
            if has_hangul(row.color):
                row.color = ru_color(row.color)
                dirty = True
            if has_hangul(getattr(row, "generation", None)):
                row.generation = dehangul(row.generation)  # type: ignore[attr-defined]
                dirty = True
            if dirty:
                listings_changed += 1

        reports = (await session.execute(select(InspectionReport))).scalars().all()
        for rep in reports:
            dirty = False
            if has_hangul(rep.inspector):
                rep.inspector = dehangul(rep.inspector)[:64]
                dirty = True
            if has_hangul(rep.summary):
                rep.summary = dehangul(rep.summary)
                dirty = True
            marks = rep.marks if isinstance(rep.marks, list) else []
            new_marks = []
            for m in marks:
                if isinstance(m, dict) and has_hangul(m.get("note")):
                    m = {**m, "note": dehangul(m["note"])[:300]}
                    dirty = True
                new_marks.append(m)
            if new_marks != marks:
                rep.marks = json.loads(json.dumps(new_marks, ensure_ascii=False))
            items = rep.items if isinstance(rep.items, list) else []
            new_items = []
            for it in items:
                if isinstance(it, dict) and has_hangul(it.get("note")):
                    it = {**it, "note": dehangul(it["note"])[:300]}
                    dirty = True
                new_items.append(it)
            if new_items != items:
                rep.items = json.loads(json.dumps(new_items, ensure_ascii=False))
            if dirty:
                reports_changed += 1

        await session.commit()
    print(
        f"done: {listings_changed} listings, {reports_changed} inspection reports "
        "переведены",
        flush=True,
    )


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
