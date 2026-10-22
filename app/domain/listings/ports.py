"""Порты объявлений: чтение для сценариев и запись при ингесте."""

from __future__ import annotations

from typing import Mapping, Protocol, Sequence

from app.domain.listings.ingestion import IngestStats
from app.domain.listings.unified import UnifiedListing


class ListingRepository(Protocol):
    """Чтение объявлений для прикладного слоя."""

    async def exists_active(self, listing_id: int) -> bool:
        """Существует ли активное объявление с таким id."""

    async def cards_by_ids(self, listing_ids: list[int]) -> dict:
        """Карточки объявлений по id (`ListingCard` из application.dto)."""


class ListingWriter(Protocol):
    """Запись объявлений: upsert, история цен, raw_key, деактивация."""

    async def upsert_many(
        self, items: Sequence[UnifiedListing], raw_keys: Mapping[int, str]
    ) -> IngestStats:
        """Upsert пачки объявлений с точками цены и `raw_keys` (id → ключ S3).

        Возвращает статистику вместе с `pending_uploads` — объектами,
        которым нужен raw в хранилище.
        """

    async def attach_raw_keys(self, raw_keys: Mapping[int, str]) -> None:
        """Записать `listings.raw_key` после успешной загрузки raw в S3."""

    async def mark_stale(self, *, max_age_days: int | None = None) -> list[int]:
        """Деактивировать объявления, не виденные дольше порога.

        Источники в circuit breaker пропускаются. Возвращает id
        деактивированных.
        """


class DuplicateGuard(Protocol):
    """МежИсточниковая дедупликация (Redis-ключи по VIN/fuzzy)."""

    async def is_duplicate(self, item: UnifiedListing) -> bool:
        """`True`, если объявление уже встречалось у другого источника."""
