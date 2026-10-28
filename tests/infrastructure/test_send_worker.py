"""Тесты Send Worker (этап 4): instant с rate limit и антидублем,
тихие часы → digest-буфер, скан фильтров, дайджест."""

from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest

import app.infrastructure.workers.send as send_mod
from app.infrastructure.db.models import Favorite, Listing, SentLog, User, UserFilter
from app.infrastructure.services.notify import (
    collect_sold_favorites,
    notified_key,
    read_digest,
)
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


def _user_stub(tier: str = "pro") -> Any:
    """Пользователь для tier-aware дайджеста: активный pro с действующей датой."""
    from datetime import UTC as _UTC, datetime as _dt, timedelta as _td

    return SimpleNamespace(
        telegram_id=100,
        is_active=True,
        subscription_tier=tier,
        subscription_until=_dt.now(_UTC) + _td(days=5),
    )


def _patch_get(session: FakeSession) -> None:
    async def _get(model: Any, pk: Any) -> Any:
        if model is Listing:
            return _listing(pk)
        if model is User:
            return _user_stub()
        return None

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

    result = await send_mod.digest_job_morning(ctx)

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

    result = await send_mod.digest_job_morning(ctx)

    assert result == {"sent": 0, "kept": 1}
    assert await read_digest(fake_redis, 100) == [5]  # не потеряно


@pytest.mark.asyncio
async def test_digest_job_empty_skips_user(fake_redis):
    ctx = {"redis": fake_redis, "bot_token": "t"}
    result = await send_mod.digest_job_morning(ctx)
    assert result == {"sent": 0, "kept": 0}


def test_worker_registers_jobs_and_cron():
    assert set(send_mod.HANDLERS) == {
        "send_listing_notification",
        "send_listing_status",
        "notify_new_listings",
        "listing_status_scan",
        "digest_job",
        "digest_job_morning",
        "digest_job_evening",
        "subscription_check_job",
        "favorites_sold_job",
        "market_placement_expiry",
    }
    crons = send_mod.CRON_JOBS
    assert {(c.hour, c.minute) for c in crons} == {
        (9, 0),
        (20, 0),
        (12, 0),
        (4, 0),
        (4, 30),
        (3, 40),
    }
    assert {c.name for c in crons} == {
        "digest_job_morning",
        "digest_job_evening",
        "subscription_check_job",
        "dialog_cleanup_job",
        "favorites_sold_job",
        "market_placement_expiry",
    }


# --- экспирация размещений рынка (06 §2-§3) ------------------------------------


class _FakeCentrifugo:
    """Стаб CentrifugoPublisher: копит публикации каналов."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def publish(self, channel: str, data: dict) -> None:
        self.calls.append((channel, dict(data)))


def _owner_lot(
    lot_id: int,
    owner_id: int,
    *,
    free_until: Any = None,
    paid_until: Any = None,
) -> Any:
    """Опубликованный (accepted) user-лот рынка с заданными placement-датами."""
    return SimpleNamespace(
        id=lot_id,
        owner_type="user",
        owner_user_id=owner_id,
        is_active=True,
        placement_free_until=free_until,
        placement_paid_until=paid_until,
        status="accepted",
    )


def _owner_user(
    owner_id: int, tier: str = "free", until: Any = None
) -> Any:
    """Владелец лота: effective_tier читает tier/until (None — бессрочный)."""
    return SimpleNamespace(
        telegram_id=owner_id,
        subscription_tier=tier,
        subscription_until=until,
    )


@pytest.mark.asyncio
async def test_market_expiry_deactivates_expired_and_publishes(
    ctx, patched_session
):
    """Просроченный free-лот получает status='expired' (is_active НЕ
    трогаем — 10 §1); в user:{id} уходит listing_status."""
    from datetime import UTC as _UTC, datetime as _dt, timedelta as _td

    session = patched_session
    now = _dt.now(_UTC)
    expired = _owner_lot(
        11, 100, free_until=now - _td(days=1)
    )
    # Второй владелец: оплаченный до будущего — не трогаем
    paid_alive = _owner_lot(12, 200, paid_until=now + _td(days=5))
    session.set_results(
        [[expired, paid_alive], [_owner_user(100), _owner_user(200)]]
    )
    centrifugo = _FakeCentrifugo()
    ctx["centrifugo"] = centrifugo

    result = await send_mod.market_placement_expiry(ctx)

    assert result == {"downgraded": 0, "expired": 1}
    # 10 §1: экспиратор пишет status вместо is_active=false — is_active
    # остаётся true для совместимости старых фильтров.
    assert expired.status == "expired"
    assert expired.is_active is True
    assert paid_alive.is_active is True
    assert paid_alive.status == "accepted"
    assert centrifugo.calls == [
        (
            "user:100",
            {"type": "listing_status", "listing_id": 11, "status": "expired"},
        )
    ]
    assert session.committed is True


@pytest.mark.asyncio
async def test_market_expiry_downgrades_included_lot_for_free_user(
    ctx, patched_session
):
    """Included-лот у free-владельца получает free_until=now+7d один раз."""
    from datetime import UTC as _UTC, datetime as _dt, timedelta as _td

    session = patched_session
    before = _dt.now(_UTC)
    included = _owner_lot(21, 300)  # без дат — был включённым лотом тарифа
    session.set_results([[included], [_owner_user(300)]])
    centrifugo = _FakeCentrifugo()
    ctx["centrifugo"] = centrifugo

    result = await send_mod.market_placement_expiry(ctx)

    assert result == {"downgraded": 1, "expired": 0}
    assert included.is_active is True
    free_until = included.placement_free_until
    assert (
        _td(days=6, hours=23)
        < free_until.replace(tzinfo=_UTC) - before
        < _td(days=7, minutes=5)
    )
    assert centrifugo.calls == []


@pytest.mark.asyncio
async def test_market_expiry_skips_paid_tier_included_lots(
    ctx, patched_session
):
    """Владелец на платном тарифе: включённый лот не трогаем (§3)."""
    from datetime import UTC as _UTC, datetime as _dt, timedelta as _td

    session = patched_session
    included = _owner_lot(31, 400)
    pro = _owner_user(400, "pro", _dt.now(_UTC) + _td(days=5))
    session.set_results([[included], [pro]])
    centrifugo = _FakeCentrifugo()
    ctx["centrifugo"] = centrifugo

    result = await send_mod.market_placement_expiry(ctx)

    assert result == {"downgraded": 0, "expired": 0}
    assert included.is_active is True
    assert included.placement_free_until is None
    assert included.placement_paid_until is None
    assert centrifugo.calls == []


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


# --- проданные избранного (требование владельца) --------------------------------


@pytest.mark.asyncio
async def test_collect_sold_favorites_sql_filters(session, fake_redis):
    """Интеграционно (Postgres): в выборку идут только неактивные/исчезнувшие
    объявления, по которым не было отправки kind='sold_fav'."""
    from datetime import UTC as _UTC, datetime as _dt, timedelta as _td

    user = User(
        telegram_id=930001,
        role="buyer",
        subscription_tier="free",
        is_active=True,
        created_at=_dt.now(_UTC),
    )
    session.add(user)

    def _mk(source_id: str, *, is_active: bool = True, disappeared=None) -> Listing:
        item = Listing(
            source="encar",
            source_id=source_id,
            url=f"https://encar.com/{source_id}",
            is_active=is_active,
            disappeared_at=disappeared,
        )
        session.add(item)
        return item

    active = _mk("sold-fav-active")
    sold = _mk("sold-fav-inactive", is_active=False)
    vanished = _mk("sold-fav-vanished", disappeared=_dt.now(_UTC))
    already = _mk("sold-fav-already", is_active=False)
    await session.flush()

    base = _dt.now(_UTC).replace(microsecond=0)
    for i, item in enumerate((active, sold, vanished, already)):
        session.add(
            Favorite(
                user_id=user.telegram_id,
                listing_id=item.id,
                created_at=base + _td(seconds=i),
            )
        )
    # по этому объявлению уведомление уже было — не выбираем повторно
    session.add(
        SentLog(user_id=user.telegram_id, listing_id=already.id, kind="sold_fav")
    )
    await session.flush()

    pairs = await collect_sold_favorites(session, fake_redis)

    assert [lid for _, lid, _ in pairs] == [sold.id, vanished.id]


@pytest.mark.asyncio
async def test_collect_sold_favorites_skips_redis_notified(ctx, patched_session):
    """Redis-ключ notified:{uid}:{lid}:sold_fav — второй слой антидубля."""
    session = patched_session
    listing_sold = _listing(5)
    listing_sold.is_active = False
    session.set_result([(100, listing_sold)])
    await ctx["redis"].setex(notified_key(100, 5, "sold_fav"), 600, "1")

    assert await collect_sold_favorites(session, ctx["redis"]) == []

    # SQL содержит неактивность (is_active/disappeared_at) и JOIN антидубля
    sql = str(session.executed[0].compile(compile_kwargs={"literal_binds": True}))
    assert "disappeared_at" in sql and "sold_fav" in sql and "sent_log" in sql


def test_format_sold_favorites_card_single_and_multi():
    single = send_mod.format_sold_favorites_card([_listing(5)])
    assert single == (
        "🚗 <b>Hyundai Palisade 2022</b> продана — убрали из избранного. "
        "Ищем похожие?"
    )

    multi = send_mod.format_sold_favorites_card([_listing(5), _listing(6)])
    assert multi.count("•") == 2
    assert "• <b>Hyundai Palisade 2022</b>" in multi
    assert multi.endswith("Ищем похожие?")


def _patch_sold_send(monkeypatch: pytest.MonkeyPatch, sent_calls: list) -> None:
    """Стаб send_telegram_message с reply_markup (фикстура patched_session
    патчит только 3 аргумента)."""

    async def _send(bot_token, chat_id, text, reply_markup=None):
        sent_calls.append((chat_id, text, reply_markup))
        return True, None

    monkeypatch.setattr(send_mod, "send_telegram_message", _send)


@pytest.mark.asyncio
async def test_favorites_sold_job_batches_marks_and_removes(
    ctx, patched_session, monkeypatch
):
    session = patched_session
    session.set_result([(100, _listing(5)), (100, _listing(6))])
    monkeypatch.setattr(
        send_mod,
        "get_settings",
        lambda: SimpleNamespace(miniapp_url="https://webapp.test"),
    )
    sent_calls: list[tuple[int, str, dict | None]] = []
    _patch_sold_send(monkeypatch, sent_calls)

    result = await send_mod.favorites_sold_job(ctx)

    assert result == {"removed": 2, "notified": 2, "send_failed": 0}
    # одному юзеру с двумя проданными — одно сообщение со списком
    assert len(sent_calls) == 1
    chat_id, text, markup = sent_calls[0]
    assert chat_id == 100
    assert "Palisade" in text and "Ищем похожие" in text
    # кнопки «Ищем похожие»: web_app (https) на /analyses?similar={listing_id}
    assert markup is not None
    urls = [b["web_app"]["url"] for row in markup["inline_keyboard"] for b in row]
    assert urls == [
        "https://webapp.test/analyses?similar=5",
        "https://webapp.test/analyses?similar=6",
    ]
    # favorite удалён (в любом случае), антидубль kind='sold_fav' записан
    assert any("DELETE FROM favorites" in str(stmt) for stmt in session.executed)
    assert any("INSERT INTO sent_log" in str(stmt) for stmt in session.executed)
    assert await ctx["redis"].get(notified_key(100, 5, "sold_fav")) == "1"
    assert await ctx["redis"].get(notified_key(100, 6, "sold_fav")) == "1"


@pytest.mark.asyncio
async def test_favorites_sold_job_send_failure_still_removes(
    ctx, patched_session, monkeypatch
):
    """Требование владельца: favorite удаляется даже при ошибке отправки."""
    session = patched_session
    session.set_result([(100, _listing(5))])
    monkeypatch.setattr(
        send_mod, "get_settings", lambda: SimpleNamespace(miniapp_url="")
    )

    async def _fail(*_a: Any, **_kw: Any) -> tuple[bool, str | None]:
        return False, "boom"

    monkeypatch.setattr(send_mod, "send_telegram_message", _fail)

    result = await send_mod.favorites_sold_job(ctx)

    assert result == {"removed": 1, "notified": 0, "send_failed": 1}
    assert any("DELETE FROM favorites" in str(stmt) for stmt in session.executed)
    # отправка не прошла — антидубль не пишем (favorite уже удалён)
    assert not any("INSERT INTO sent_log" in str(stmt) for stmt in session.executed)
    assert await ctx["redis"].get(notified_key(100, 5, "sold_fav")) is None


@pytest.mark.asyncio
async def test_favorites_sold_job_caps_batch_at_five(ctx, patched_session, monkeypatch):
    """Больше 5 проданных — сообщение с первыми 5, хвост следующим прогоном."""
    session = patched_session
    session.set_result([(100, _listing(i)) for i in range(1, 7)])
    monkeypatch.setattr(
        send_mod, "get_settings", lambda: SimpleNamespace(miniapp_url="")
    )
    sent_calls: list[tuple[int, str, dict | None]] = []
    _patch_sold_send(monkeypatch, sent_calls)

    result = await send_mod.favorites_sold_job(ctx)

    assert result["removed"] == 5
    assert len(sent_calls) == 1  # одно сообщение, а не шесть