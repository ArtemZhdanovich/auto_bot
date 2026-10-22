"""Тесты Send Worker (этап 4): instant с rate limit и антидублем,
тихие часы → digest-буфер, скан фильтров, дайджест."""

from decimal import Decimal
from typing import Any

import pytest

import app.infrastructure.workers.send as send_mod
from app.infrastructure.db.models import Listing, SentLog, UserFilter
from app.infrastructure.services.notify import notified_key, read_digest
from tests.conftest import FakeRedis, FakeSession


def _listing(listing_id: int = 5) -> Listing:
    return Listing(
        id=listing_id,
        source="encar",
        source_id=f"S{listing_id}",
        url=f"https://encar.com/{listing_id}",
        make="Hyundai",
        model="Palisade",
        year=2022,
        price=Decimal("37000"),
        currency="USD",
        mileage_km=42000,
    )


class SessionCtx:
    """Обёртка FakeSession в async context manager (как session_factory)."""

    def __init__(self, session: FakeSession) -> None:
        self.session = session

    async def __aenter__(self) -> FakeSession:
        return self.session

    async def __aexit__(self, *args: Any) -> None:
        return None


class FakePublisher:
    """Стаб TaskPublisher: копит постановки задач."""

    def __init__(self) -> None:
        self.jobs: list[tuple[str, tuple]] = []

    async def enqueue(self, name: str, *args: Any, **kwargs: Any) -> str:
        self.jobs.append((name, args))
        return "msg-id"


@pytest.fixture
def ctx(fake_redis: FakeRedis) -> dict[str, Any]:
    return {"redis": fake_redis, "bot_token": "test-token"}


@pytest.fixture
def patched_session(monkeypatch: pytest.MonkeyPatch):
    """Подменяет session_factory в модуле send и telegram-отправку на ok."""
    session = FakeSession()

    class _Factory:
        def __call__(self) -> SessionCtx:
            return SessionCtx(session)

    monkeypatch.setattr(send_mod, "session_factory", _Factory())

    async def _send(bot_token: str, chat_id: int, text: str) -> tuple[bool, str | None]:
        session.sent = (chat_id, text)  # type: ignore[attr-defined]
        return True, None

    monkeypatch.setattr(send_mod, "send_telegram_message", _send)
    return session


def _patch_get(session: FakeSession) -> None:
    async def _get(model: Any, pk: Any) -> Any:
        return _listing(pk) if model is Listing else None

    session.get = _get  # type: ignore[method-assign]


# --- instant ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_listing_success(ctx, patched_session):
    session = patched_session
    _patch_get(session)

    result = await send_mod.send_listing_notification(ctx, user_id=100, listing_id=5)

    assert result == {"user_id": 100, "listing_id": 5, "sent": True}
    assert await ctx["redis"].get(notified_key(100, 5)) == "1"
    # sent_log записан через pg_insert
    assert any("sent_log" in str(stmt) for stmt in session.executed)


@pytest.mark.asyncio
async def test_send_listing_rate_limited(ctx, patched_session):
    await ctx["redis"].set("rl:notify:100", "1", nx=True, ex=30)
    result = await send_mod.send_listing_notification(ctx, user_id=100, listing_id=5)
    assert result["skipped"] == "rate_limited"


@pytest.mark.asyncio
async def test_send_listing_already_sent(ctx, patched_session):
    await ctx["redis"].setex(notified_key(100, 5), 600, "1")
    result = await send_mod.send_listing_notification(ctx, user_id=100, listing_id=5)
    assert result["skipped"] == "already_sent"


@pytest.mark.asyncio
async def test_send_listing_not_found(ctx, patched_session):
    result = await send_mod.send_listing_notification(ctx, user_id=100, listing_id=999)
    assert result["error"] == "not_found"


@pytest.mark.asyncio
async def test_quiet_hours_go_to_digest(ctx, patched_session, monkeypatch):
    session = patched_session
    session.set_result(["23:00-08:00"])  # _user_quiet_hours
    _patch_get(session)
    monkeypatch.setattr(send_mod, "in_quiet_hours", lambda *_: True)

    result = await send_mod.send_listing_notification(ctx, user_id=100, listing_id=5)

    assert result["skipped"] == "quiet_hours"
    # не потеряно: ушло в буфер дайджеста, sent_log пуст
    assert await read_digest(ctx["redis"], 100) == [5]
    assert not any(isinstance(o, SentLog) for o in session.added)


# --- скан фильтров ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_notify_new_listings_dispatch(ctx, monkeypatch):
    flt_instant = UserFilter(user_id=100, notify_mode="instant", criteria={})
    flt_digest = UserFilter(user_id=200, notify_mode="digest", criteria={})

    async def fake_collect(session, redis, mode, window_sec=None):
        if mode == "instant":
            return [(flt_instant, [_listing(1), _listing(2)])]
        return [(flt_digest, [_listing(3)])]

    monkeypatch.setattr(send_mod, "collect_for_filters", fake_collect)
    pool = FakePublisher()
    ctx["publisher"] = pool

    counts = await send_mod.notify_new_listings(ctx)

    assert counts == {"instant": 2, "digest": 1}
    assert pool.jobs == [
        ("send_listing_notification", (100, 1)),
        ("send_listing_notification", (100, 2)),
    ]
    assert await read_digest(ctx["redis"], 200) == [3]


@pytest.mark.asyncio
async def test_notify_new_listings_without_pool_buffers_only(ctx, monkeypatch):
    flt = UserFilter(user_id=100, notify_mode="instant", criteria={})

    async def fake_collect(session, redis, mode, window_sec=None):
        return [(flt, [_listing(7)])] if mode == "instant" else []

    monkeypatch.setattr(send_mod, "collect_for_filters", fake_collect)
    ctx["publisher"] = None

    counts = await send_mod.notify_new_listings(ctx)
    assert counts == {"instant": 1, "digest": 0}


# --- дайджест --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_digest_job_sends_and_clears(ctx, patched_session):
    session = patched_session
    fake_redis = ctx["redis"]
    await fake_redis.rpush("digest:100", "5", "6")
    _patch_get(session)

    result = await send_mod.digest_job(ctx)

    assert result == {"sent": 2, "kept": 0}
    chat_id, text = session.sent  # type: ignore[attr-defined]
    assert chat_id == 100
    assert "Дайджест" in text and "Palisade" in text
    # буфер очищен, антидубль записан
    assert await read_digest(fake_redis, 100) == []
    assert await fake_redis.get(notified_key(100, 5)) == "1"
    assert await fake_redis.get(notified_key(100, 6)) == "1"


@pytest.mark.asyncio
async def test_digest_job_send_failure_keeps_buffer(ctx, patched_session, monkeypatch):
    session = patched_session
    fake_redis = ctx["redis"]
    await fake_redis.rpush("digest:100", "5")

    async def _fail(*_a: Any) -> tuple[bool, str | None]:
        return False, "boom"

    monkeypatch.setattr(send_mod, "send_telegram_message", _fail)
    _patch_get(session)

    result = await send_mod.digest_job(ctx)

    assert result == {"sent": 0, "kept": 1}
    assert await read_digest(fake_redis, 100) == [5]  # не потеряно


@pytest.mark.asyncio
async def test_digest_job_empty_skips_user(fake_redis):
    ctx = {"redis": fake_redis, "bot_token": "t"}
    result = await send_mod.digest_job(ctx)
    assert result == {"sent": 0, "kept": 0}


def test_worker_registers_jobs_and_cron():
    assert set(send_mod.HANDLERS) == {
        "send_listing_notification",
        "send_listing_status",
        "notify_new_listings",
        "listing_status_scan",
        "digest_job",
        "subscription_check_job",
    }
    crons = send_mod.CRON_JOBS
    assert {(c.hour, c.minute) for c in crons} == {(9, 0), (20, 0), (12, 0), (4, 0)}
    assert {c.name for c in crons} == {
        "digest_job",
        "subscription_check_job",
        "dialog_cleanup_job",
    }


# --- статусные уведомления (фаза 3) --------------------------------------------


@pytest.mark.asyncio
async def test_send_listing_status_price_drop(ctx, patched_session, monkeypatch):
    session = patched_session
    _patch_get(session)
    # тихие часы не мешают: стаб сессии возвращает строки цены на все execute()
    async def _no_quiet(*_a):
        return None

    monkeypatch.setattr(send_mod, "_user_quiet_hours", _no_quiet)
    # точки цены (desc): новая 42000, предыдущая 40000 → «было 40 000»
    session.set_result([Decimal("42000"), Decimal("40000")])

    result = await send_mod.send_listing_status(ctx, 100, 5, kind="price_drop")

    assert result == {"user_id": 100, "listing_id": 5, "kind": "price_drop", "sent": True}
    _chat_id, text = session.sent  # type: ignore[attr-defined]
    assert "Цена снизилась" in text and "40 000" in text
    assert await ctx["redis"].get(notified_key(100, 5, "price_drop")) == "1"


@pytest.mark.asyncio
async def test_send_listing_status_gone(ctx, patched_session):
    session = patched_session
    _patch_get(session)

    result = await send_mod.send_listing_status(ctx, 100, 5, kind="gone")

    assert result["sent"] is True
    _chat_id, text = session.sent  # type: ignore[attr-defined]
    assert "исчезло" in text
    assert await ctx["redis"].get(notified_key(100, 5, "gone")) == "1"


@pytest.mark.asyncio
async def test_send_listing_status_already_sent_per_kind(ctx, patched_session):
    await ctx["redis"].setex(notified_key(100, 5, "returned"), 600, "1")

    result = await send_mod.send_listing_status(ctx, 100, 5, kind="returned")

    assert result["skipped"] == "already_sent"


@pytest.mark.asyncio
async def test_listing_status_scan_dispatch(ctx, monkeypatch):
    async def fake_audience(session, listing_id):
        return [100, 200]

    monkeypatch.setattr(send_mod, "event_audience", fake_audience)
    pool = FakePublisher()
    ctx["publisher"] = pool

    result = await send_mod.listing_status_scan(ctx, "gone", [5])

    assert result == {"kind": "gone", "dispatched": 2}
    assert pool.jobs == [
        ("send_listing_status", (100, 5, "gone")),
        ("send_listing_status", (200, 5, "gone")),
    ]