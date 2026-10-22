"""Unit-тесты правил ингеста и сценария IngestListings.

Без БД, Redis и сети: доменные функции проверяются напрямую, сценарий —
на фейковых портах.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Mapping, Sequence

from app.application.listings.ingest import IngestListings
from app.domain.listings.ingestion import (
    ExistingState,
    IngestStats,
    PendingRawUpload,
    compute_content_hash,
    decide,
    summarize,
)
from app.domain.listings.unified import UnifiedListing


def _item(**kw: Any) -> UnifiedListing:
    base: dict[str, Any] = {
        "source": "encar",
        "source_id": "1",
        "url": "https://encar.com/a/1",
        "title": "Hyundai Palisade 2022",
        "make": "Hyundai",
        "model": "Palisade",
        "year": 2022,
        "price": Decimal("38500"),
        "currency": "USD",
        "mileage_km": 50000,
        "images": ["https://img.example/1.jpg"],
    }
    base.update(kw)
    return UnifiedListing(**base)


def _previous(item: UnifiedListing, **kw: Any) -> ExistingState:
    base: dict[str, Any] = {
        "listing_id": 7,
        "price": item.price,
        "currency": item.currency,
        "content_hash": compute_content_hash(item),
        "is_active": True,
    }
    base.update(kw)
    return ExistingState(**base)


# ---------- доменные правила ----------


def test_content_hash_ignores_parse_time_and_image_order() -> None:
    t1 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    a = _item(
        parsed_at=t1,
        images=["https://img.example/2.jpg", "https://img.example/1.jpg"],
    )
    b = _item(
        parsed_at=t1 + timedelta(days=1),
        images=["https://img.example/1.jpg", "https://img.example/2.jpg"],
    )
    assert a.images != b.images  # порядок разный — хэш всё равно один
    assert compute_content_hash(a) == compute_content_hash(b)
    assert compute_content_hash(a) != compute_content_hash(_item(price=Decimal("1")))


def test_decide_new_and_unchanged() -> None:
    item = _item()
    brand_new = decide(
        item, listing_id=7, content_hash=compute_content_hash(item), previous=None
    )
    assert brand_new.kind == "new"
    assert brand_new.price_point is True  # первая точка цены
    assert brand_new.needs_translation and brand_new.needs_raw_upload

    same = decide(
        item,
        listing_id=7,
        content_hash=compute_content_hash(item),
        previous=_previous(item),
    )
    assert same.kind == "unchanged"
    assert same.price_point is False
    assert not same.needs_translation and not same.needs_raw_upload


def test_decide_price_change_and_reactivation() -> None:
    item = _item()
    cheaper = _previous(item, price=Decimal("40000"), content_hash="c" * 64)
    changed = decide(
        item, listing_id=7, content_hash=compute_content_hash(item), previous=cheaper
    )
    assert changed.kind == "price_changed"
    assert changed.price_point is True
    assert changed.needs_translation  # контент изменился → перевод заново

    returned = decide(
        item,
        listing_id=7,
        content_hash=compute_content_hash(item),
        previous=_previous(item, is_active=False, price=Decimal("40000")),
    )
    assert returned.kind == "reactivated"
    assert returned.price_point is True
    assert not returned.needs_raw_upload  # объект уже лежит в S3

    same_price = decide(
        _item(mileage_km=60000),
        listing_id=7,
        content_hash="b" * 64,
        previous=_previous(item),
    )
    assert same_price.kind == "updated"
    assert same_price.price_point is False


def test_summarize_counts_ids_and_uploads() -> None:
    item = _item()
    upload = PendingRawUpload(
        key="raw/encar/2026/10/7_hash.json",
        listing_id=7,
        content_hash=compute_content_hash(item),
        item=item,
    )
    outcomes = [
        decide(item, listing_id=7, content_hash="x" * 64, previous=None),
        decide(
            item,
            listing_id=8,
            content_hash=compute_content_hash(item),
            previous=_previous(item),
        ),
        decide(
            item,
            listing_id=9,
            content_hash="y" * 64,
            previous=_previous(item, price=Decimal("1")),
        ),
        decide(
            item,
            listing_id=10,
            content_hash="z" * 64,
            previous=_previous(item, is_active=False),
        ),
    ]
    stats = summarize(outcomes, cross_dup=2, uploads=[upload])
    assert (stats.new, stats.unchanged, stats.price_changed, stats.reactivated) == (
        1,
        1,
        1,
        1,
    )
    assert stats.updated == 0 and stats.cross_dup == 2
    assert stats.price_changed_ids == (9,)
    assert stats.reactivated_ids == (10,)
    assert stats.translate_ids == (7, 9)  # NEW + смена контента
    assert stats.pending_uploads == (upload,)


# ---------- сценарий на фейковых портах ----------


class FakeWriter:
    """Порт `ListingWriter`: отдаёт заготовленную статистику, помнит ключи."""

    def __init__(self, stats: IngestStats) -> None:
        self.stats = stats
        self.upserted: list[UnifiedListing] = []
        self.attached: dict[int, str] | None = None

    async def upsert_many(
        self, items: Sequence[UnifiedListing], raw_keys: Mapping[int, str]
    ) -> IngestStats:
        self.upserted = list(items)
        return self.stats

    async def attach_raw_keys(self, raw_keys: Mapping[int, str]) -> None:
        self.attached = dict(raw_keys)

    async def mark_stale(self, *, max_age_days: int | None = None) -> list[int]:
        return []


class FakeStorage:
    """Порт `ObjectStoragePort`: складывает тела объектов в память."""

    def __init__(self, broken: set[str] | None = None) -> None:
        self.puts: list[tuple[str, bytes, str]] = []
        self.broken = broken or set()

    async def put(
        self,
        key: str,
        body: bytes,
        content_type: str,
        metadata: dict | None = None,
    ) -> None:
        if key in self.broken:
            raise RuntimeError("s3 down")
        self.puts.append((key, body, content_type))


class FakeDuplicates:
    """Порт `DuplicateGuard`: дубли — по списку source_id."""

    def __init__(self, duplicates: set[str]) -> None:
        self.duplicates = duplicates

    async def is_duplicate(self, item: UnifiedListing) -> bool:
        return item.source_id in self.duplicates


async def test_ingest_skips_duplicates_uploads_raw_and_attaches_keys() -> None:
    item = _item()
    content_hash = compute_content_hash(item)
    upload = PendingRawUpload(
        key="raw/encar/2026/10/7_hash.json",
        listing_id=7,
        content_hash=content_hash,
        item=item,
    )
    writer = FakeWriter(
        summarize(
            [decide(item, listing_id=7, content_hash=content_hash, previous=None)],
            uploads=[upload],
        )
    )
    storage = FakeStorage()
    use_case = IngestListings(
        writer,
        storage,
        duplicates=FakeDuplicates({"dup"}),
        payload_of=lambda i: {"source": i.source, "raw": i.raw},
    )

    stats = await use_case.execute([item, _item(source_id="dup")])

    assert writer.upserted == [item]  # дубль отфильтрован до записи
    assert stats.cross_dup == 1
    assert stats.new == 1
    assert [key for key, _body, _ct in storage.puts] == [upload.key]
    body = json.loads(storage.puts[0][1])
    assert body["source"] == "encar" and storage.puts[0][2] == "application/json"
    assert writer.attached == {7: upload.key}
