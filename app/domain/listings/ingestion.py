"""Чистые правила ингеста объявлений (PLAN.md, фазы 1–2, 6).

Ни SQLAlchemy, ни Redis, ни HTTP: только доменные правила, которые можно
проверить без инфраструктуры.

Три группы правил:
- идентичность контента — `compute_content_hash`;
- подготовка полей к записи — `row_values`/`clean_value`;
- статистика — `decide` (судьба одного объявления) и `summarize`
  (счётчики и id-списки для уведомлений и перевода).

Справочники синонимов (кузов/топливо/КПП/привод) живут в
`infrastructure/sources/common/normalize.py`: домен их не импортирует,
канонизацию по словарям применяет адаптер записи перед SQL.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Iterable, Literal, Mapping, Sequence

from app.domain.listings.unified import UnifiedListing

# Исходы одного объявления после upsert. Взаимоисключающие: ровно один
# счётчик в `IngestStats` увеличивается на объявление.
OutcomeKind = Literal["new", "reactivated", "unchanged", "updated", "price_changed"]

# Ветки, где тайтл мог измениться → нужен перевод и новый raw-объект.
_CONTENT_CHANGED: frozenset[str] = frozenset({"new", "updated", "price_changed"})


def compute_content_hash(item: UnifiedListing) -> str:
    """sha256(title|price|currency|mileage_km|sorted(images)).

    Без parsed_at/updated_at (меняются при каждом парсинге) и без description
    (поля нет в контракте). Смена хэша = «объявление изменилось»; в фазе 5
    этот же хэш — триггер повторного перевода (PLAN.md, раздел 1).
    """
    payload = "|".join(
        [
            item.title or "",
            str(item.price or ""),
            item.currency or "",
            str(item.mileage_km or ""),
            ",".join(sorted(str(u) for u in item.images)),
        ]
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def clean_value(raw: str | None) -> str | None:
    """Trim значения; пустая строка → `None` (не пишем пустоту в БД)."""
    value = (raw or "").strip()
    return value or None


def row_values(item: UnifiedListing) -> dict[str, Any]:
    """Параметры VALUES для upsert.

    Поля нормализуются на уровне «мусора»: trim, пустое → NULL, URL и
    картинки в строки. Спеки (кузов/топливо/КПП/привод/объём/цвет) здесь
    только очищаются: канонизация по справочникам — в адаптере записи,
    потому что словари синонимов лежат в инфраструктурном слое.
    """
    return {
        "source": item.source,
        "source_id": item.source_id,
        "url": str(item.url),
        "title_raw": item.title,
        "make": item.make,
        "model": item.model,
        "year": item.year,
        "price": item.price,
        "currency": item.currency,
        "mileage_km": item.mileage_km,
        "vin": item.vin,
        "body_type": clean_value(item.body_type),
        "fuel": clean_value(item.fuel),
        "transmission": clean_value(item.transmission),
        "drive": clean_value(item.drive),
        "engine_volume": item.engine_volume,
        "color": clean_value(item.color),
        "images": [str(i) for i in item.images],
        "options": [str(o) for o in item.options],
        "schema_version": item.schema_version,
        "parsed_at": item.parsed_at,
    }


def audit_payload(item: UnifiedListing) -> dict[str, Any]:
    """Минимальный raw-payload для аудита (перепарсинг, PLAN.md фаза 6).

    Вызывающий слой может передать в сценарий более полный построитель
    (например, с внутренними dedup-ключами) — форма объекта его забота.
    """
    return {
        "source": item.source,
        "schema_version": item.schema_version,
        "raw": item.raw,
    }


@dataclass(frozen=True, slots=True)
class ExistingState:
    """Строка `listings` до upsert (CTE `existing` в SQL)."""

    listing_id: int
    price: Decimal | None
    currency: str | None
    content_hash: str | None
    is_active: bool

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> ExistingState | None:
        """Состояние из RETURNING-upsert; `None` — объявления ещё не было."""
        if row["old_id"] is None:
            return None
        return cls(
            listing_id=row["listing_id"],
            price=row["old_price"],
            currency=row["old_currency"],
            content_hash=row["old_hash"],
            is_active=bool(row["was_active"]),
        )


@dataclass(frozen=True, slots=True)
class PendingRawUpload:
    """Сырой payload, который надо положить в объектное хранилище."""

    key: str
    listing_id: int
    content_hash: str
    item: UnifiedListing


@dataclass(frozen=True, slots=True)
class ItemOutcome:
    """Что делать с одним объявлением после upsert."""

    kind: OutcomeKind
    listing_id: int
    content_hash: str
    item: UnifiedListing
    price_point: bool

    @property
    def needs_translation(self) -> bool:
        """NEW или изменение контента → тайтл мог измениться (фаза 5)."""
        return self.kind in _CONTENT_CHANGED

    @property
    def needs_raw_upload(self) -> bool:
        """NEW или изменение контента → новый объект в S3 (фаза 6)."""
        return self.kind in _CONTENT_CHANGED


def decide(
    item: UnifiedListing,
    *,
    listing_id: int,
    content_hash: str,
    previous: ExistingState | None,
) -> ItemOutcome:
    """Судьба объявления: NEW / реактивация / без изменений / цена / контент.

    Args:
        item: Объявление из источника.
        listing_id: id строки в `listings` (для NEW — только что созданной).
        content_hash: Хэш текущего парсинга.
        previous: Состояние строки до upsert; `None` — объявление новое.

    Returns:
        Исход и признаки побочных эффектов (точка цены, перевод, S3).
    """
    price_changed = item.price is not None and (
        previous is None
        or previous.price is None
        or item.price != previous.price
        or item.currency != previous.currency
    )
    if previous is None:  # NEW — первая точка цены
        return ItemOutcome(
            kind="new",
            listing_id=listing_id,
            content_hash=content_hash,
            item=item,
            price_point=item.price is not None,
        )
    if not previous.is_active:  # вернулось в выдачу
        return ItemOutcome(
            kind="reactivated",
            listing_id=listing_id,
            content_hash=content_hash,
            item=item,
            price_point=price_changed,
        )
    if previous.content_hash == content_hash:  # без изменений
        return ItemOutcome(
            kind="unchanged",
            listing_id=listing_id,
            content_hash=content_hash,
            item=item,
            price_point=False,
        )
    kind: OutcomeKind = "price_changed" if price_changed else "updated"
    return ItemOutcome(
        kind=kind,
        listing_id=listing_id,
        content_hash=content_hash,
        item=item,
        price_point=price_changed,
    )


@dataclass(frozen=True, slots=True)
class IngestStats:
    """Итог ингеста: счётчики + id-списки для уведомлений и перевода.

    `cross_dup` — отброшенные межИсточниковые дубли (Redis, TTL 48 ч);
    `pending_uploads` — raw-объекты, которые сценарий ещё должен положить
    в хранилище, прежде чем писать `raw_key`.
    """

    new: int = 0
    updated: int = 0
    unchanged: int = 0
    price_changed: int = 0
    reactivated: int = 0
    cross_dup: int = 0
    price_changed_ids: tuple[int, ...] = ()
    reactivated_ids: tuple[int, ...] = ()
    translate_ids: tuple[int, ...] = ()
    pending_uploads: tuple[PendingRawUpload, ...] = ()

    @property
    def touched(self) -> bool:
        """Были ли изменения (для лога «listings_saved»)."""
        return any(
            (
                self.new,
                self.updated,
                self.unchanged,
                self.price_changed,
                self.reactivated,
                self.cross_dup,
            )
        )


def summarize(
    outcomes: Iterable[ItemOutcome],
    *,
    cross_dup: int = 0,
    uploads: Sequence[PendingRawUpload] = (),
) -> IngestStats:
    """Свернуть исходы в счётчики и id-списки.

    Args:
        outcomes: Исходы по объявлениям в порядке обработки.
        cross_dup: Сколько объявлений отброшено как межИсточниковые дубли.
        uploads: Подготовленные raw-объекты к загрузке.

    Returns:
        Статистика ингеста.
    """
    items = list(outcomes)
    counters = Counter(outcome.kind for outcome in items)
    return IngestStats(
        new=counters["new"],
        updated=counters["updated"],
        unchanged=counters["unchanged"],
        price_changed=counters["price_changed"],
        reactivated=counters["reactivated"],
        cross_dup=cross_dup,
        price_changed_ids=tuple(
            o.listing_id for o in items if o.kind == "price_changed"
        ),
        reactivated_ids=tuple(o.listing_id for o in items if o.kind == "reactivated"),
        translate_ids=tuple(o.listing_id for o in items if o.needs_translation),
        pending_uploads=tuple(uploads),
    )
