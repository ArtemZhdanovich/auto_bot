"""Локальная чистка данных после пересбора (прод-ревизия 2026-10-24).

1. Зацикленные переводы NLLB (model «Tesla yko model yko…», «old man.»):
   model пересчитывается из title_raw через глоссарий; если после глоссария
   остался хангыль — одна попытка NLLB; если снова мусор — только первые
   3 значимых слова без повторов.
2. Остатки хангыля в make/model/color — вторая попытка перевода.
3. kcar без VIN/привода/объёма — батч-обогащение из detail-API.

Запуск ЛОКАЛЬНО (без Docker): .venv/Scripts/python scripts/fix_data_quality.py
"""

from __future__ import annotations

import asyncio
import logging
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.domain.translation.glossary import apply_glossary  # noqa: E402
from app.infrastructure.db.models import Listing  # noqa: E402
from app.infrastructure.db.session import session_factory  # noqa: E402
from app.infrastructure.services.translate_client import (  # noqa: E402
    translate_client_from_settings,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("fix_data")

HANGUL = re.compile("[\uAC00-\uD7A3]")
# Повтор биграммы/слова ≥3 раз или хвост из повторов — признак цикла NLLB.
LOOP = re.compile(r"(\b\S+\s+\S+\b)(?:\s+\1){2,}", re.IGNORECASE)
LOOP_WORD = re.compile(r"(\b\S+\b)(?:\s+\1){3,}", re.IGNORECASE)


def is_garbage(value: str | None) -> bool:
    if not value:
        return False
    return bool(LOOP.search(value) or LOOP_WORD.search(value) or "yko" in value or "old man" in value.lower())


def dedupe_words(text: str, limit: int = 4) -> str:
    seen: list[str] = []
    for word in text.split():
        if word.lower() not in [w.lower() for w in seen]:
            seen.append(word)
        if len(seen) >= limit:
            break
    return " ".join(seen)


async def main() -> None:
    translator = translate_client_from_settings()

    async with session_factory() as session:
        rows = (await session.execute(select(Listing))).scalars().all()
        fixed_loops = fixed_hangul = 0
        need_translate: dict[int, dict[str, str]] = {}

        for row in rows:
            # 1) зацикленный/мусорный model → пересчёт из title_raw
            if is_garbage(row.model):
                base = row.title_raw or row.title_ru or row.model
                candidate = apply_glossary(base)
                if HANGUL.search(candidate):
                    need_translate.setdefault(row.id, {})["model"] = candidate
                    row.model = row.model  # оставляем до перевода
                    fixed_loops += 1
                    continue
                row.model = dedupe_words(candidate) or None
                fixed_loops += 1
                continue
            # 2) остатки хангыля
            for field in ("make", "model", "generation", "color"):
                value = getattr(row, field, None)
                if value and HANGUL.search(value):
                    prepared = apply_glossary(value)
                    if HANGUL.search(prepared):
                        need_translate.setdefault(row.id, {})[field] = prepared
                    else:
                        setattr(row, field, prepared or value)
                        fixed_hangul += 1

        # Батч-перевод накопленного
        if need_translate:
            pairs: list[tuple[int, str, str]] = [
                (lid, field, text)
                for lid, fields in need_translate.items()
                for field, text in fields.items()
            ]
            try:
                translated = await translator.translate(
                    [t for _, _, t in pairs], source_lang="ko", target_lang="ru"
                )
            except Exception:  # noqa: BLE001
                translated = None
            if translated:
                by_id = {row.id: row for row in rows}
                for (lid, field, _), value in zip(pairs, translated):
                    if not value or is_garbage(value):
                        continue
                    setattr(by_id[lid], field, dedupe_words(value, limit=6))
                    fixed_hangul += 1

        await session.commit()
        logger.info("fixed loops=%d hangul=%d", fixed_loops, fixed_hangul)

    # 3) kcar-обогащение спеков (VIN/привод/объём) — без Docker, локально
    from app.infrastructure.services.fetch import enrich_listing_specs

    enriched = 0
    async with session_factory() as session:
        rows = (
            (
                await session.execute(
                    select(Listing).where(
                        Listing.source == "kcar", Listing.specs_enriched_at.is_(None)
                    )
                )
            )
            .scalars()
            .all()
        )
        for row in rows:
            try:
                if await enrich_listing_specs(session, row):
                    enriched += 1
            except Exception:  # noqa: BLE001
                logger.warning("enrich failed id=%s", row.id, exc_info=True)
        await session.commit()
    logger.info("kcar enriched=%d/%d", enriched, len(rows))


if __name__ == "__main__":
    asyncio.run(main())
