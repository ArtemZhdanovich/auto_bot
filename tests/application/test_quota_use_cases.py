"""Сценарии квоты AI-поиска: ключ, суточное окно, allow/consume (без Redis)."""

from datetime import UTC, datetime

from app.application.quota.use_cases import (
    quota_allow,
    quota_consume,
    quota_key,
    quota_used,
)


class _Store:
    """Заглушка `QuotaStore` в памяти."""

    def __init__(self) -> None:
        self.counts: dict[str, int] = {}
        self.ttls: dict[str, int] = {}

    async def get_int(self, key: str) -> int:
        return self.counts.get(key, 0)

    async def add(self, key: str, delta: int) -> int:
        self.counts[key] = self.counts.get(key, 0) + delta
        return self.counts[key]

    async def expire(self, key: str, ttl_sec: int) -> None:
        self.ttls[key] = ttl_sec


def test_key_is_daily_utc():
    now = datetime(2026, 10, 21, 23, 59, tzinfo=UTC)
    assert quota_key(7, now) == "ai_quota:7:20261021"


async def test_consume_sets_daily_ttl_only_on_first_write():
    store = _Store()

    assert await quota_consume(store, 7) == 1
    assert store.ttls[quota_key(7)] == 86400

    store.ttls.clear()
    assert await quota_consume(store, 7) == 2
    assert store.ttls == {}  # окно не растягивается повторами


async def test_release_compensates_failed_search():
    store = _Store()
    await quota_consume(store, 7)

    assert await quota_consume(store, 7, delta=-1) == 0
    assert await quota_used(store, 7) == 0


async def test_allow_respects_limit_and_unlimited():
    store = _Store()
    await quota_consume(store, 7)

    assert await quota_allow(store, 7, limit=2) is True
    await quota_consume(store, 7)
    assert await quota_allow(store, 7, limit=2) is False
    assert await quota_allow(store, 7, limit=None) is True


async def test_used_is_zero_for_empty_counter():
    assert await quota_used(_Store(), 404) == 0
