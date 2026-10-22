"""Тесты хранилища диалога: Redis-адаптер, репозиторий архива, сценарии.

`RedisDialogStore` проверяется на `FakeRedis` (TTL, порча JSON), SQL —
на тестовой сессии: `owned_session` фильтрует по владельцу (IDOR),
`archive` обновляет ту же строку, `last_session` отдаёт последнюю.
"""

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.dialog.use_cases import (
    FinalizeDialog,
    ResetDialog,
    RestoreDialog,
    TriggerFetch,
)
from app.application.dialog.dto import DialogFinalizeCommand
from app.domain.dialog.state import Message, new_state
from app.domain.search.criteria import SearchCriteria
from app.infrastructure.cache.dialog_store import RedisDialogStore, dialog_key
from app.infrastructure.db.models import DialogSession, Source, UserFilter
from app.infrastructure.db.repositories.dialog import SqlAlchemyDialogRepository

from conftest import FakeRedis


class _Sources:
    """Заглушка `EnabledSourcesPort`."""

    def __init__(self, names: list[str]) -> None:
        self._names = names

    async def enabled_source_names(self) -> list[str]:
        return list(self._names)


class _Queue:
    """Заглушка `FetchQueuePort`: запоминает постановки."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def enqueue_source(self, name: str, payload: dict) -> None:
        self.calls.append((name, payload))


# --- Redis-хранилище ---------------------------------------------


@pytest.mark.asyncio
async def test_store_roundtrip_with_ttl(fake_redis: FakeRedis):
    store = RedisDialogStore(fake_redis, ttl_sec=123)
    state = new_state(42)
    await store.save(state)

    assert fake_redis.ttls[dialog_key(42)] == 123
    loaded = await store.load(42)
    assert loaded is not None
    assert loaded.session_id == state.session_id
    assert loaded.messages[-1].content == "Опишите, какую машину ищете."


@pytest.mark.asyncio
async def test_store_load_missing_and_corrupted(fake_redis: FakeRedis):
    store = RedisDialogStore(fake_redis)
    assert await store.load(1) is None
    fake_redis.data[dialog_key(1)] = "not json"
    assert await store.load(1) is None  # повреждённое состояние → как отсутствующее


@pytest.mark.asyncio
async def test_store_delete(fake_redis: FakeRedis):
    store = RedisDialogStore(fake_redis)
    await store.save(new_state(7))
    await store.delete(7)
    assert await store.load(7) is None


@pytest.mark.asyncio
async def test_state_messages_capped(fake_redis: FakeRedis):
    """TECHDEBT 4.9: история обрезается до MAX_STATE_MESSAGES при save."""
    from app.infrastructure.cache.dialog_store import MAX_STATE_MESSAGES

    store = RedisDialogStore(fake_redis)
    state = new_state(11)
    state.messages = [
        Message(role="user", content=f"msg {i}") for i in range(MAX_STATE_MESSAGES + 30)
    ]
    await store.save(state)
    loaded = await store.load(11)
    assert loaded is not None
    assert len(loaded.messages) == MAX_STATE_MESSAGES
    # остались последние сообщения, не первые
    assert loaded.messages[-1].content == f"msg {MAX_STATE_MESSAGES + 29}"


# --- сценарии на фейковой сессии ---------------------------------


@pytest.mark.asyncio
async def test_reset_archives_and_deletes(fake_redis, fake_session):
    store = RedisDialogStore(fake_redis)
    state = new_state(5)
    state.messages.append(Message(role="user", content="Хочу Palisade"))
    await store.save(state)

    await ResetDialog(store, SqlAlchemyDialogRepository(fake_session)).execute(5)

    assert fake_session.committed
    assert await store.load(5) is None
    archives = [o for o in fake_session.added if isinstance(o, DialogSession)]
    assert len(archives) == 1
    assert archives[0].status == "done"
    assert isinstance(archives[0].criteria, dict)


@pytest.mark.asyncio
async def test_finalize_creates_filter_enqueues_and_archives(fake_redis, fake_session):
    store = RedisDialogStore(fake_redis)
    state = new_state(9)
    state.criteria = SearchCriteria(make="Kia", model="Sorento", price_max=45000)
    await store.save(state)

    queue = _Queue()
    use_case = FinalizeDialog(
        store,
        SqlAlchemyDialogRepository(fake_session),
        TriggerFetch(_Sources(["encar", "kcar"]), queue),
    )
    result = await use_case.execute(DialogFinalizeCommand(user_id=9))

    assert list(result.sources) == ["encar", "kcar"]
    assert result.criteria["make"] == "Kia"
    assert [name for name, _ in queue.calls] == ["encar", "kcar"]
    assert fake_session.committed
    assert await store.load(9) is None  # состояние сброшено

    filters = [o for o in fake_session.added if isinstance(o, UserFilter)]
    archives = [o for o in fake_session.added if isinstance(o, DialogSession)]
    assert len(filters) == 1
    assert filters[0].criteria["model"] == "Sorento"
    assert len(archives) == 1
    assert archives[0].status == "searching"
    assert archives[0].finished_at is not None


# --- restore (диалог виден спустя время) ---------------------------


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
    store = RedisDialogStore(fake_redis)
    result = await RestoreDialog(store, SqlAlchemyDialogRepository(fake_session)).execute(5)

    assert result is not None
    assert result.restored is True
    assert result.session_id != str(row.id)  # поднятая сессия получила новый id
    state = await store.load(5)
    assert state is not None
    assert state.criteria.make == "Kia"
    assert state.messages[-1].content == "Какой год?"
    assert state.status == "collecting"


@pytest.mark.asyncio
async def test_restore_skips_active_dialog(fake_redis: FakeRedis, fake_session):
    store = RedisDialogStore(fake_redis)
    await store.save(new_state(5))
    result = await RestoreDialog(store, SqlAlchemyDialogRepository(fake_session)).execute(5)

    assert result is not None
    assert result.restored is False
    assert result.reason == "active"
    assert fake_session.executed == []  # в PG не ходили


@pytest.mark.asyncio
async def test_restore_without_archive(fake_redis: FakeRedis, fake_session):
    fake_session.set_result([])
    store = RedisDialogStore(fake_redis)
    result = await RestoreDialog(store, SqlAlchemyDialogRepository(fake_session)).execute(5)
    assert result is None


# --- репозиторий на тестовой БД -----------------------------------


@pytest.mark.asyncio
async def test_repository_archive_upserts_single_row(session: AsyncSession):
    repo = SqlAlchemyDialogRepository(session)
    state = new_state(101)
    state.messages.append(Message(role="user", content="Palisade"))
    await repo.archive(state)
    await repo.commit()

    state.turn_count = 7
    await repo.archive(state)
    await repo.commit()

    rows = (
        (
            await session.execute(
                select(DialogSession).where(
                    DialogSession.id == uuid.UUID(state.session_id)
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].turn_count == 7
    assert rows[0].user_id == 101


@pytest.mark.asyncio
async def test_repository_owned_session_filters_by_user(session: AsyncSession):
    owner, other = 201, 202
    row = DialogSession(
        id=uuid.uuid4(),
        user_id=owner,
        status="done",
        criteria={"make": "Kia"},
        turn_count=1,
        messages=[{"role": "user", "content": "секрет"}],
    )
    session.add(row)
    await session.commit()

    repo = SqlAlchemyDialogRepository(session)
    own = await repo.owned_session(str(row.id), owner)
    assert own is not None and own.session_id == str(row.id)
    assert await repo.owned_session(str(row.id), other) is None
    assert await repo.owned_session("not-a-uuid", owner) is None


@pytest.mark.asyncio
async def test_repository_last_session_returns_latest(session: AsyncSession):
    user_id = 301
    old = DialogSession(
        id=uuid.uuid4(),
        user_id=user_id,
        status="done",
        criteria={},
        turn_count=1,
        messages=[{"role": "user", "content": "старый"}],
        created_at=datetime.now(UTC) - timedelta(days=1),
        updated_at=datetime.now(UTC) - timedelta(days=1),
    )
    fresh = DialogSession(
        id=uuid.uuid4(),
        user_id=user_id,
        status="done",
        criteria={},
        turn_count=2,
        messages=[{"role": "user", "content": "новый"}],
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    session.add_all([old, fresh])
    await session.commit()

    record = await SqlAlchemyDialogRepository(session).last_session(user_id)
    assert record is not None
    assert record.session_id == str(fresh.id)


@pytest.mark.asyncio
async def test_repository_enabled_source_names(session: AsyncSession):
    session.add(
        Source(name="encar", adapter_class="EncarApiAdapter", kind="api", enabled=True)
    )
    session.add(
        Source(name="kcar", adapter_class="KcarApiAdapter", kind="api", enabled=False)
    )
    await session.commit()

    names = await SqlAlchemyDialogRepository(session).enabled_source_names()
    assert names == ["encar"]


# --- legacy-сервис диалога (FAQ и чистка) -------------------------
#
# Эти функции остаются в `infrastructure/services/dialog.py`: FAQ-кэш
# использует агент, чистку — cron. Тесты перенесены сюда из старого файла,
# чтобы покрытие не потерялось при выделении адаптера.


@pytest.mark.asyncio
async def test_faq_answer_cached(fake_redis: FakeRedis):
    from app.infrastructure.services.dialog import answer_faq

    first = await answer_faq(fake_redis, "customs_kr_ru")
    assert first and "растаможка" in first.lower()
    assert "faq:customs_kr_ru" in fake_redis.data  # попал в кэш
    cached = await answer_faq(fake_redis, "customs_kr_ru")
    assert cached == first
    assert await answer_faq(fake_redis, "no_such_topic") is None


@pytest.mark.asyncio
async def test_cleanup_expired_dialogs(fake_session):
    from app.infrastructure.services.dialog import cleanup_expired_dialogs

    deleted = await cleanup_expired_dialogs(fake_session)
    assert deleted == 0  # FakeSession.rowcount отсутствует → 0
    assert fake_session.committed
    assert fake_session.executed  # выполнен DELETE-запрос
