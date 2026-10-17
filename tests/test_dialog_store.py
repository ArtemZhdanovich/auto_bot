"""Тесты DialogStore: Redis TTL, load/save/delete, архивация, финализация."""

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.schemas.criteria import SearchCriteria
from app.services.dialog import (
    DialogStore,
    answer_faq,
    cleanup_expired_dialogs,
    dialog_key,
    finalize_dialog,
    new_state,
    reset_dialog,
    restore_dialog,
)
from app.schemas.dialog import Message

from conftest import FakeRedis


@pytest.mark.asyncio
async def test_store_roundtrip_with_ttl(fake_redis: FakeRedis):
    store = DialogStore(fake_redis, ttl_sec=123)
    state = new_state(42)
    await store.save(state)

    assert fake_redis.ttls[dialog_key(42)] == 123
    loaded = await store.load(42)
    assert loaded is not None
    assert loaded.session_id == state.session_id
    assert loaded.messages[-1].content == "Опишите, какую машину ищете."


@pytest.mark.asyncio
async def test_store_load_missing_and_corrupted(fake_redis: FakeRedis):
    store = DialogStore(fake_redis)
    assert await store.load(1) is None
    fake_redis.data[dialog_key(1)] = "not json"
    assert await store.load(1) is None  # повреждённое состояние → как отсутствующее


@pytest.mark.asyncio
async def test_store_delete(fake_redis: FakeRedis):
    store = DialogStore(fake_redis)
    await store.save(new_state(7))
    await store.delete(7)
    assert await store.load(7) is None


@pytest.mark.asyncio
async def test_faq_answer_cached(fake_redis: FakeRedis):
    first = await answer_faq(fake_redis, "customs_kr_ru")
    assert first and "растаможка" in first.lower()
    assert "faq:customs_kr_ru" in fake_redis.data  # попал в кэш
    cached = await answer_faq(fake_redis, "customs_kr_ru")
    assert cached == first
    assert await answer_faq(fake_redis, "no_such_topic") is None


@pytest.mark.asyncio
async def test_reset_archives_and_deletes(fake_redis, fake_session):
    store = DialogStore(fake_redis)
    state = new_state(5)
    state.messages.append(Message(role="user", content="Хочу Palisade"))
    await store.save(state)

    await reset_dialog(5, session=fake_session, store=store)

    assert fake_session.committed
    assert await store.load(5) is None
    # в сессию попал архив DialogSession
    from app.db.models import DialogSession

    archives = [o for o in fake_session.added if isinstance(o, DialogSession)]
    assert len(archives) == 1
    assert archives[0].status == "done"
    assert archives[0].criteria["make"] is None or isinstance(archives[0].criteria, dict)


@pytest.mark.asyncio
async def test_finalize_creates_filter_enqueues_and_archives(fake_redis, fake_session):
    store = DialogStore(fake_redis)
    state = new_state(9)
    state.criteria = SearchCriteria(make="Kia", model="Sorento", price_max=45000)
    await store.save(state)

    enqueued: list[SearchCriteria] = []

    async def fake_enqueue(criteria: SearchCriteria) -> list[str]:
        enqueued.append(criteria)
        return ["encar", "kcar"]

    result = await finalize_dialog(
        9, session=fake_session, store=store, enqueue=fake_enqueue
    )

    assert result["sources"] == ["encar", "kcar"]
    assert result["criteria"]["make"] == "Kia"
    assert enqueued and enqueued[0].make == "Kia"
    assert fake_session.committed
    assert await store.load(9) is None  # состояние сброшено

    from app.db.models import DialogSession, UserFilter

    filters = [o for o in fake_session.added if isinstance(o, UserFilter)]
    archives = [o for o in fake_session.added if isinstance(o, DialogSession)]
    assert len(filters) == 1
    assert filters[0].criteria["model"] == "Sorento"
    assert len(archives) == 1
    assert archives[0].status == "searching"
    assert archives[0].finished_at is not None


# --- restore (диалог виден спустя время) и чистка через сутки --------------


def _archive_row(user_id: int = 5) -> SimpleNamespace:
    """Строка dialog_sessions из PG (архив завершённого диалога)."""
    return SimpleNamespace(
        id=uuid.uuid4(),
        user_id=user_id,
        status="done",
        criteria={"make": "Kia", "model": "Sorento"},
        turn_count=4,
        messages=[
            {"role": "user", "content": "Kia Sorento до 35к"},
            {"role": "assistant", "content": "Какой год?"},
        ],
        created_at=datetime.now(UTC) - timedelta(hours=2),
        updated_at=datetime.now(UTC) - timedelta(minutes=5),
    )


@pytest.mark.asyncio
async def test_restore_dialog_from_archive(fake_redis: FakeRedis, fake_session):
    row = _archive_row()
    fake_session.set_result([row])
    result = await restore_dialog(5, session=fake_session, store=DialogStore(fake_redis))

    assert result and result["restored"] is True
    assert result["session_id"] != str(row.id)  # поднятая сессия получила новый id
    state = await DialogStore(fake_redis).load(5)
    assert state is not None
    assert state.criteria.make == "Kia"
    assert state.messages[-1].content == "Какой год?"
    assert state.status == "collecting"


@pytest.mark.asyncio
async def test_restore_skips_active_dialog(fake_redis: FakeRedis, fake_session):
    await DialogStore(fake_redis).save(new_state(5))
    result = await restore_dialog(5, session=fake_session, store=DialogStore(fake_redis))
    assert result == {"restored": False, "reason": "active"}
    assert fake_session.executed == []  # в PG не ходили


@pytest.mark.asyncio
async def test_restore_without_archive(fake_redis: FakeRedis, fake_session):
    fake_session.set_result([])
    result = await restore_dialog(5, session=fake_session, store=DialogStore(fake_redis))
    assert result is None


@pytest.mark.asyncio
async def test_cleanup_expired_dialogs(fake_session):
    deleted = await cleanup_expired_dialogs(fake_session)
    assert deleted == 0  # FakeSession.rowcount отсутствует → 0
    assert fake_session.committed
    assert fake_session.executed  # выполнен DELETE-запрос
