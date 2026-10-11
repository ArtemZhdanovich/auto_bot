"""Тесты compute_content_hash / save_listings (PLAN.md, фазы 1–2)."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.schemas.listing import UnifiedListing
from app.services.fetch import compute_content_hash, save_listings
from tests.conftest import FakeRedis


def _item(**kw) -> UnifiedListing:
    base: dict = {
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
        "images": ["https://img.example/1.jpg", "https://img.example/2.jpg"],
    }
    base.update(kw)
    return UnifiedListing(**base)


def _row(
    item: UnifiedListing,
    *,
    old_id: int | None = 1,
    was_active: bool = True,
    old_price: Decimal | None = None,
    old_currency: str | None = None,
    old_hash: str | None = None,
) -> dict:
    return {
        "old_id": old_id,
        "old_price": item.price if old_price is None else old_price,
        "old_currency": item.currency if old_currency is None else old_currency,
        "old_hash": compute_content_hash(item) if old_hash is None else old_hash,
        "was_active": was_active,
        "listing_id": old_id or 7,
    }


NEW_ROW = {
    "old_id": None,
    "old_price": None,
    "old_currency": None,
    "old_hash": None,
    "was_active": None,
    "listing_id": 7,
}


class _Result:
    def __init__(self, row):
        self._row = row

    def mappings(self):
        return self

    def first(self):
        return self._row


class UpsertSession:
    """Стаб сессии: execute() отдаёт заготовленные строки по очереди."""

    def __init__(self, rows):
        self._queue = list(rows)
        self.executed: list[tuple[str, dict | None]] = []
        self.committed = False

    async def execute(self, stmt, params=None):
        self.executed.append((str(stmt), params))
        return _Result(self._queue.pop(0) if self._queue else None)

    async def commit(self):
        self.committed = True


# ---------- compute_content_hash ----------


def test_content_hash_stable_when_only_timestamps_change():
    t1 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    a = _item(parsed_at=t1)
    b = _item(parsed_at=t1 + timedelta(days=1))
    assert compute_content_hash(a) == compute_content_hash(b)


def test_content_hash_changes_on_price_with_same_title():
    assert compute_content_hash(_item()) != compute_content_hash(
        _item(price=Decimal("40000"))
    )


def test_content_hash_changes_on_mileage_with_same_price():
    assert compute_content_hash(_item()) != compute_content_hash(_item(mileage_km=60000))


def test_content_hash_changes_on_currency_and_title():
    assert compute_content_hash(_item()) != compute_content_hash(_item(currency="KRW"))
    assert compute_content_hash(_item()) != compute_content_hash(_item(title="Kia"))


def test_content_hash_ignores_image_order():
    reordered = _item(images=["https://img.example/2.jpg", "https://img.example/1.jpg"])
    assert compute_content_hash(_item()) == compute_content_hash(reordered)


# ---------- save_listings ----------


async def test_new_listing_writes_first_price_point():
    session = UpsertSession([NEW_ROW])
    stats = await save_listings(session, None, [_item()])
    assert stats == {
        "new": 1,
        "updated": 0,
        "unchanged": 0,
        "price_changed": 0,
        "reactivated": 0,
        "cross_dup": 0,
    }
    assert session.committed is True
    assert len(session.executed) == 2  # upsert + первая точка цены
    assert "listing_price_history" in session.executed[1][0]
    assert session.executed[1][1] == {
        "listing_id": 7,
        "price": Decimal("38500"),
        "currency": "USD",
    }


async def test_unchanged_listing_skips_price_point():
    session = UpsertSession([_row(_item())])
    stats = await save_listings(session, None, [_item()])
    assert stats["unchanged"] == 1 and stats["new"] == 0
    assert len(session.executed) == 1  # только upsert


async def test_price_change_writes_price_point():
    session = UpsertSession(
        [_row(_item(), old_price=Decimal("40000"), old_hash="a" * 64)]
    )
    stats = await save_listings(session, None, [_item()])
    assert stats["price_changed"] == 1 and stats["updated"] == 0
    assert len(session.executed) == 2
    assert "listing_price_history" in session.executed[1][0]
    assert session.executed[1][1]["price"] == Decimal("38500")


async def test_non_price_change_without_price_point():
    session = UpsertSession([_row(_item(mileage_km=60000), old_hash="b" * 64)])
    stats = await save_listings(session, None, [_item()])
    assert stats["updated"] == 1 and stats["price_changed"] == 0
    assert len(session.executed) == 1


async def test_reactivation_resets_disappeared_at():
    session = UpsertSession([_row(_item(), was_active=False)])
    stats = await save_listings(session, None, [_item()])
    assert stats["reactivated"] == 1
    assert "disappeared_at = NULL" in session.executed[0][0]
    assert len(session.executed) == 1  # цена не менялась — точки нет


async def test_reactivation_with_price_change_writes_point():
    session = UpsertSession(
        [_row(_item(), was_active=False, old_price=Decimal("40000"), old_hash="a" * 64)]
    )
    stats = await save_listings(session, None, [_item()])
    assert stats["reactivated"] == 1
    assert len(session.executed) == 2
    assert "listing_price_history" in session.executed[1][0]


async def test_cross_source_dup_checked_before_upsert():
    redis = FakeRedis()
    first = UpsertSession([NEW_ROW])
    await save_listings(first, redis, [_item()])

    second = UpsertSession([])
    stats = await save_listings(second, redis, [_item()])
    assert stats["cross_dup"] == 1
    assert second.executed == []  # до upsert дело не дошло
