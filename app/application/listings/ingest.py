"""Сценарий ингеста: сохранить объявления источника (PLAN.md, фазы 1–2, 6).

Порядок важен:
1. межИсточниковая дедупликация — ДО записи в БД (порт `DuplicateGuard`);
2. upsert пачки + точки истории цен (порт `ListingWriter`);
3. raw_payload NEW/изменившихся — в объектное хранилище (порт
   `ObjectStoragePort`), ключ объекта пишется в `listings.raw_key`.

Ошибка загрузки отдельного объекта не ломает сохранение объявлений:
`raw_key` остаётся NULL (fallback, фаза 6).

Сценарий не знает про SQLAlchemy, Redis, httpx и aioboto3 — только порты.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from typing import Any

from app.application.catalog.ports import ObjectStoragePort
from app.domain.listings.ingestion import (
    IngestStats,
    PendingRawUpload,
    audit_payload,
)
from app.domain.listings.ports import DuplicateGuard, ListingWriter
from app.domain.listings.unified import UnifiedListing


class IngestListings:
    """Сохранить порцию объявлений источника с отслеживанием изменений."""

    def __init__(
        self,
        writer: ListingWriter,
        storage: ObjectStoragePort | None = None,
        *,
        duplicates: DuplicateGuard | None = None,
        payload_of: Callable[[UnifiedListing], Mapping[str, Any]] = audit_payload,
    ) -> None:
        """
        Args:
            writer: Запись объявлений (upsert, история цен, raw_key).
            storage: Объектное хранилище raw_payload; `None` — не грузим.
            duplicates: Гвард межИсточниковых дублей; `None` — не проверяем.
            payload_of: Построитель raw-объекта; по умолчанию минимальный
                аудит-payload, вызывающий слой может передать полный.
        """
        self._writer = writer
        self._storage = storage
        self._duplicates = duplicates
        self._payload_of = payload_of

    async def execute(self, items: Sequence[UnifiedListing]) -> IngestStats:
        """Выполнить ингест пачки объявлений.

        Args:
            items: Объявления из источника в порядке выдачи.

        Returns:
            Статистика: счётчики, id-списки событий и `pending_uploads`.
        """
        kept, cross_dup = await self._drop_duplicates(items)
        stats = await self._writer.upsert_many(kept, {})
        if cross_dup:
            stats = replace(stats, cross_dup=stats.cross_dup + cross_dup)
        raw_keys = await self._upload_raw(stats.pending_uploads)
        if raw_keys:
            await self._writer.attach_raw_keys(raw_keys)
        return stats

    async def _drop_duplicates(
        self, items: Sequence[UnifiedListing]
    ) -> tuple[list[UnifiedListing], int]:
        """Отфильтровать межИсточниковые дубли; вернуть (оставшиеся, кол-во)."""
        if self._duplicates is None:
            return list(items), 0
        kept: list[UnifiedListing] = []
        dropped = 0
        for item in items:
            if await self._duplicates.is_duplicate(item):
                dropped += 1
                continue
            kept.append(item)
        return kept, dropped

    async def _upload_raw(self, uploads: Sequence[PendingRawUpload]) -> dict[int, str]:
        """Загрузить raw-объекты параллельно; вернуть ключи успешных.

        Ошибка отдельной загрузки не влияет на остальные и на сохранение
        объявлений: её ключ просто не попадёт в `listings.raw_key`.
        """
        if self._storage is None or not uploads:
            return {}
        results = await asyncio.gather(
            *(
                self._storage.put(
                    upload.key,
                    self._body(upload),
                    "application/json",
                    metadata={
                        "source": upload.item.source,
                        "listing_id": upload.listing_id,
                        "content_hash": upload.content_hash,
                        "fetched_at": upload.item.parsed_at,
                    },
                )
                for upload in uploads
            ),
            return_exceptions=True,
        )
        return {
            upload.listing_id: upload.key
            for upload, result in zip(uploads, results)
            if not isinstance(result, BaseException)
        }

    def _body(self, upload: PendingRawUpload) -> bytes:
        """JSON-тело объекта: одинаковый для любого хранилища формат."""
        payload = self._payload_of(upload.item)
        return json.dumps(payload, ensure_ascii=False, default=str).encode()
