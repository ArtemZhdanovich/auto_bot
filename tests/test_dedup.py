"""Тесты дедупликации (ТЗ п. 6.10)."""

from app.adapters.common.dedup import fuzzy_key, is_duplicate, vin_key
from app.schemas.listing import UnifiedListing
from tests.conftest import FakeRedis


def _listing(**overrides) -> UnifiedListing:
    defaults = dict(
        source="encar",
        source_id="1",
        url="https://encar.com/1",
        make="Hyundai",
        model="Palisade",
        year=2022,
        mileage_km=42000,
        price=38500000,
        currency="KRW",
    )
    defaults.update(overrides)
    return UnifiedListing(**defaults)


def test_vin_key_deterministic_and_normalized():
    assert vin_key("ABC123") == vin_key(" abc123 ")
    assert vin_key("ABC123") != vin_key("XYZ789")
    assert vin_key("ABC123").startswith("dedup:vin:")


def test_fuzzy_key_same_car():
    assert fuzzy_key(_listing()) == fuzzy_key(_listing(source="kcar", source_id="999"))


def test_fuzzy_key_different_car():
    assert fuzzy_key(_listing()) != fuzzy_key(_listing(year=2023))


async def test_is_duplicate_first_time_false(fake_redis: FakeRedis):
    assert await is_duplicate(fake_redis, _listing(vin="VIN123")) is False
    assert await is_duplicate(fake_redis, _listing(vin="VIN123")) is True


async def test_is_duplicate_cross_source(fake_redis: FakeRedis):
    first = _listing()  # без VIN → fuzzy
    second = _listing(source="kcar", source_id="777")
    assert await is_duplicate(fake_redis, first) is False
    assert await is_duplicate(fake_redis, second) is True
