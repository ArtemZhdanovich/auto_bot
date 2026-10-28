"""Безопасный бэкфилл существующих отчётов осмотра (окт 2026).

НЕ трогает marks (полный enrich при лежащем mdsl перезаписал бы отчёты
без карты повреждений). Делает строго добавления:
1. `pos` (координаты метки на диаграмме Encar) каждой отметке — из
   `_ZONE_POS` адаптера, без сети;
2. факты истории ДТП из госданных (`record/vehicle/{id}/open?vehicleNo=`,
   fem «История ДТП») — добавляются в summary, если их там ещё нет;
3. корейские строки (поля объявления + inspector/notes отчёта) → русский
   через NLLB-микросервис (глоссарий + кэш).

Запуск: docker compose run --rm -v "$(pwd)/scripts:/app/scripts" worker_fetch \
  python /app/scripts/backfill_mark_pos.py
"""

import asyncio
import json

from sqlalchemy import select

from app.domain.translation.glossary import apply_glossary
from app.infrastructure.db.models import InspectionReport, Listing
from app.infrastructure.db.session import session_factory
from app.infrastructure.services.translate_client import (
    translate_client_from_settings,
)
from app.infrastructure.sources.common.http import HttpClient
from app.infrastructure.sources.encar import EncarApiAdapter, _mark_pos
from translate_db_russian import _collect, _is_korean, _replace

# Префиксы фактов истории ДТП (из _build_inspection при record_open).
_DTP_PREFIXES = (
    "история ДТП", "ДТП ", "полная гибель", "наводнение", "угонов",
    "смен владельцев", "смен госномера", "залог/кредит",
    "коммерческое использование", "использование госорганами",
    "первая регистрация",
)


async def main() -> None:
    client = translate_client_from_settings()
    adapter = EncarApiAdapter(
        source_name="encar",
        http_client=HttpClient(timeout=15),
        translator=client,
    )

    pos_added = dtp_added = translated = 0
    async with session_factory() as session:
        rows = (
            await session.execute(select(Listing).where(Listing.source == "encar"))
        ).scalars().all()
        reports = {
            r.listing_id: r
            for r in (await session.execute(select(InspectionReport))).scalars().all()
        }

        # --- 1. pos в marks (без сети) --------------------------------
        for rep in reports.values():
            marks = rep.marks if isinstance(rep.marks, list) else []
            dirty = False
            for m in marks:
                if isinstance(m, dict) and not m.get("pos"):
                    pos = _mark_pos(m.get("zone", ""))
                    if pos:
                        m["pos"] = pos
                        dirty = True
            if dirty:
                rep.marks = json.loads(json.dumps(marks, ensure_ascii=False))
                pos_added += 1
        await session.commit()
        print(f"pos добавлен в marks: {pos_added} отчётов", flush=True)

        # --- 2. история ДТП из госданных -------------------------------
        for row in rows:
            rep = reports.get(row.id)
            if rep is None or not isinstance(rep.summary, str):
                continue
            if "история ДТП" in rep.summary:
                continue  # уже есть
            try:
                detail = await adapter.http.get_json(
                    adapter._detail_api.format(source_id=row.source_id)
                )
            except Exception:  # noqa: BLE001 — не критично
                continue
            # record/open живёт под ВНУТРЕННИМ vehicleId (как чек/диагностика),
            # а не под id объявления.
            vid = str(detail.get("vehicleId") or row.source_id)
            manage = (
                detail.get("manage") if isinstance(detail.get("manage"), dict) else {}
            )
            vehicle_no = str(
                detail.get("vehicleNo") or manage.get("vehicleNo") or ""
            ).strip()
            if not vehicle_no:
                continue
            from urllib.parse import quote

            try:
                record_open = await adapter.http.get_json(
                    adapter._accident_open_api.format(source_id=vid)
                    + f"?vehicleNo={quote(vehicle_no)}"
                )
            except Exception:  # noqa: BLE001
                continue
            if not isinstance(record_open, dict):
                continue
            fresh = EncarApiAdapter._build_inspection(
                detail, None, record_open=record_open
            )
            dtp = [
                f for f in fresh["summary"].split(" · ")
                if f.startswith(_DTP_PREFIXES)
            ]
            if not dtp:
                continue
            existing = rep.summary.split(" · ")
            rep.summary = " · ".join(existing[:1] + dtp + existing[1:])
            dtp_added += 1
        await session.commit()
        print(f"история ДТП добавлена: {dtp_added} отчётов", flush=True)

        # --- 3. корейские строки → русский (NLLB) ----------------------
        if not client.enabled:
            print("translate выключен — перевод пропущен")
            await adapter.close()
            return
        strings: dict[str, str] = {}
        for row in rows:
            for f in ("make", "model", "color", "generation"):
                v = getattr(row, f, None)
                if _is_korean(v):
                    strings.setdefault(v, v)
        for rep in reports.values():
            for f in ("inspector", "summary"):
                v = getattr(rep, f, None)
                if _is_korean(v):
                    strings.setdefault(v, v)
            _collect(rep.marks, strings)
            _collect(rep.items, strings)

        print(f"корейских строк к переводу: {len(strings)}", flush=True)
        fixed: dict[str, str] = {}
        originals = list(strings)
        CHUNK = 48
        for start in range(0, len(originals), CHUNK):
            chunk = originals[start : start + CHUNK]
            prepared = [apply_glossary(t)[:500] for t in chunk]
            ru = await client.translate(prepared, source_lang="ko", target_lang="ru")
            for orig, val in zip(originals[start:], ru):
                fixed[orig] = val or orig
            print(
                f"  переведено {min(start + CHUNK, len(originals))}/{len(originals)}",
                flush=True,
            )

        for row in rows:
            dirty = False
            for f in ("make", "model", "color", "generation"):
                v = getattr(row, f, None)
                if _is_korean(v) and fixed.get(v, v) != v:
                    setattr(row, f, fixed[v][:64])
                    dirty = True
            if dirty:
                translated += 1
        for rep in reports.values():
            dirty = False
            for f in ("inspector", "summary"):
                v = getattr(rep, f, None)
                if _is_korean(v) and fixed.get(v, v) != v:
                    setattr(rep, f, fixed[v])
                    dirty = True
            if _replace(rep.marks, fixed):
                rep.marks = json.loads(json.dumps(rep.marks, ensure_ascii=False))
                dirty = True
            if _replace(rep.items, fixed):
                rep.items = json.loads(json.dumps(rep.items, ensure_ascii=False))
                dirty = True
            if dirty:
                translated += 1
        await session.commit()

    print(f"done: pos={pos_added}, dtp={dtp_added}, translated={translated}", flush=True)
    await adapter.close()


if __name__ == "__main__":
    asyncio.run(main())
