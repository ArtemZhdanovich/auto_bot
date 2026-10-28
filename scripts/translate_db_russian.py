"""Разовый перевод корейских значений в БД через микросервис (окт 2026).

Encar отдаёт марку/модель/цвет/поколение и тексты отчёта осмотра
(inspector/summary/notes) по-корейски. Скрипт собирает все строки с хангылем
из encar-строк, прогоняет через глоссарий + NLLB-микросервис
(TRANSLATE_SERVICE_URL, кэш в Redis) и записывает переводы обратно.
Идемпотентно: строки без хангыля не трогаются.

Запуск: docker compose run --rm worker_fetch python /app/scripts/translate_db_russian.py
"""

import asyncio
import json
import re
from typing import Any

from sqlalchemy import select

from app.domain.translation.glossary import apply_glossary
from app.infrastructure.db.models import InspectionReport, Listing
from app.infrastructure.db.session import session_factory
from app.infrastructure.services.translate_client import (
    translate_client_from_settings,
)

_HANGUL = re.compile(r"[\uAC00-\uD7A3]")
CHUNK = 48


def _is_korean(text: Any) -> bool:
    """True, если строка содержит хангыль."""
    return isinstance(text, str) and bool(_HANGUL.search(text))


def _collect(obj: Any, out: dict[str, str]) -> None:
    """Собрать все строки с хангылем из dict/list/str."""
    if isinstance(obj, dict):
        for v in obj.values():
            _collect(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _collect(v, out)
    elif _is_korean(obj):
        out.setdefault(obj, obj)


def _replace(obj: Any, fixed: dict[str, str]) -> bool:
    """Заменить строки по словарю перевода (in-place); True — были замены."""
    changed = False
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, str) and v in fixed:
                obj[k] = fixed[v]
                changed = True
            else:
                changed |= _replace(v, fixed)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            if isinstance(v, str) and v in fixed:
                obj[i] = fixed[v]
                changed = True
            else:
                changed |= _replace(v, fixed)
    return changed


async def main() -> None:
    client = translate_client_from_settings()
    if not client.enabled:
        print("translate service выключен (TRANSLATE_ENABLED/URL) — выходим")
        return

    listings_changed = reports_changed = 0
    async with session_factory() as session:
        rows = (
            await session.execute(select(Listing).where(Listing.source == "encar"))
        ).scalars().all()
        reports = (await session.execute(select(InspectionReport))).scalars().all()

        strings: dict[str, str] = {}
        for row in rows:
            for f in ("make", "model", "color", "generation"):
                v = getattr(row, f, None)
                if _is_korean(v):
                    strings.setdefault(v, v)
        for rep in reports:
            for f in ("inspector", "summary"):
                v = getattr(rep, f, None)
                if _is_korean(v):
                    strings.setdefault(v, v)
            _collect(rep.marks, strings)
            _collect(rep.items, strings)

        print(f"корейских строк к переводу: {len(strings)}", flush=True)

        fixed: dict[str, str] = {}
        originals = list(strings)
        for start in range(0, len(originals), CHUNK):
            chunk = originals[start : start + CHUNK]
            prepared = [apply_glossary(t)[:500] for t in chunk]
            translated = await client.translate(
                prepared, source_lang="ko", target_lang="ru"
            )
            for orig, ru in zip(originals[start:], translated):
                fixed[orig] = ru or orig
            print(
                f"  переведено {min(start + CHUNK, len(originals))}/{len(originals)}",
                flush=True,
            )

        for row in rows:
            dirty = False
            for f in ("make", "model", "color", "generation"):
                v = getattr(row, f, None)
                if _is_korean(v):
                    new = fixed.get(v, v)
                    if new != v:
                        setattr(row, f, new[:64])
                        dirty = True
            if dirty:
                listings_changed += 1

        for rep in reports:
            dirty = False
            for f in ("inspector", "summary"):
                v = getattr(rep, f, None)
                if _is_korean(v):
                    new = fixed.get(v, v)
                    if new != v:
                        setattr(rep, f, new)
                        dirty = True
            if _replace(rep.marks, fixed):
                rep.marks = json.loads(json.dumps(rep.marks, ensure_ascii=False))
                dirty = True
            if _replace(rep.items, fixed):
                rep.items = json.loads(json.dumps(rep.items, ensure_ascii=False))
                dirty = True
            if dirty:
                reports_changed += 1

        await session.commit()
    print(
        f"done: {listings_changed} listings, {reports_changed} reports переведены",
        flush=True,
    )


if __name__ == "__main__":
    asyncio.run(main())
