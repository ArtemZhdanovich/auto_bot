"""Общие фикстуры тестов."""

import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pytest
from dishka import AsyncContainer, Provider, Scope, provide
from httpx import ASGITransport, AsyncClient
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import get_settings
from app.infrastructure.security.tokens import create_access_token
from app.infrastructure.db.models import Base, Lead, Listing, PriceAnalysis, User
from app.di.container import make_container
from app.main_api import app as fastapi_app


class FakeRedis:
    """Мини-стаб Redis: get/set/setex/delete — для дедупликации и диалогов."""

    def __init__(self) -> None:
        self.data: dict[str, str] = {}
        self.ttls: dict[str, int] = {}

    async def set(self, key: str, value: str, nx: bool = False, ex: int | None = None):
        if nx and key in self.data:
            return None
        self.data[key] = value
        if ex:
            self.ttls[key] = ex
        return True

    async def setex(self, key: str, ttl: int, value: str) -> bool:
        self.data[key] = value
        self.ttls[key] = ttl
        return True

    async def get(self, key: str):
        return self.data.get(key)

    async def exists(self, key: str) -> int:
        """Аналог Redis EXISTS: 1 если ключ есть, иначе 0."""
        return 1 if key in self.data else 0

    async def mget(self, *keys: str) -> list[str | None]:
        return [self.data.get(k) for k in keys]

    async def delete(self, *keys: str) -> int:
        removed = 0
        for key in keys:
            if key in self.data:
                del self.data[key]
                removed += 1
        return removed

    async def keys(self, pattern: str = "*") -> list[str]:
        if pattern == "*":
            return list(self.data)
        # поддержка префиксных масок вида "digest:*"
        prefix = pattern.rstrip("*")
        return [k for k in self.data if k.startswith(prefix)]

    async def rpush(self, key: str, *values: str) -> int:
        self.data.setdefault(key, [])
        self.data[key].extend(values)  # список хранится как list[str]
        return len(self.data[key])

    async def lrange(self, key: str, start: int, stop: int) -> list[str]:
        data = self.data.get(key) or []
        if stop == -1:
            return list(data[start:])
        return list(data[start : stop + 1])

    async def llen(self, key: str) -> int:
        return len(self.data.get(key) or [])

    async def incr(self, key: str) -> int:
        """Инкремент счётчика (rate limit партнёрского API, guest-лимит 1.4)."""
        self.data[key] = int(self.data.get(key, 0)) + 1
        return int(self.data[key])

    async def expire(self, key: str, ttl: int) -> bool:
        if key in self.data:
            self.ttls[key] = ttl
            return True
        return False

    async def scan_iter(self, pattern: str = "*"):
        """Async-итератор по ключам (аналог SCAN)."""
        import fnmatch
        import re

        regex = fnmatch.translate(pattern)
        for key in list(self.data):
            if re.match(regex, key):
                yield key

    class _Pipeline:
        """Мини-пайплайн: команды применяются при execute()."""

        def __init__(self, owner: "FakeRedis") -> None:
            self._owner = owner
            self._ops: list[tuple] = []

        def setex(self, key: str, ttl: int, value: str) -> None:
            self._ops.append(("setex", key, ttl, value))

        def set(self, key: str, value: str, ex: int | None = None) -> None:
            self._ops.append(("set", key, value, ex))

        def delete(self, *keys: str) -> None:
            self._ops.append(("delete", keys))

        async def execute(self) -> list[bool]:
            results = []
            for op in self._ops:
                if op[0] == "setex":
                    _, key, ttl, value = op
                    results.append(await self._owner.setex(key, ttl, value))
                elif op[0] == "set":
                    _, key, value, ex = op
                    results.append(await self._owner.set(key, value, ex=ex))
                else:
                    results.append(await self._owner.delete(*op[1]))
            return results

    def pipeline(self, transaction: bool = True) -> "_Pipeline":
        return FakeRedis._Pipeline(self)


class FakeSession:
    """Стаб AsyncSession: add() копит объекты, get/execute — пусто.

    execute() возвращает заглушку результата; строки можно задать через
    set_result([...]) — под select-запросы в тестах.
    """

    def __init__(self) -> None:
        self.added: list[Any] = []
        self.executed: list[Any] = []
        self.committed = False
        self.deleted: list[Any] = []
        self._rows: list[Any] = []
        self._row_queue: list[list[Any]] = []
        self.executed_params: list[Any] = []

    def set_result(self, rows: list[Any]) -> None:
        """Один результат для всех последующих execute()."""
        self._rows = rows
        self._row_queue = []

    def set_results(self, groups: list[list[Any]]) -> None:
        """Результаты на несколько последовательных execute()
        (последняя группа — на все остальные)."""
        self._row_queue = [list(group) for group in groups]
        self._rows = groups[-1] if groups else []

    def add(self, obj: Any) -> None:
        # Имитация серверных дефолтов БД (после flush появились бы id/created_at).
        # id — max(существующих)+1: добавленные без id объекты (например User
        # с PK telegram_id) не сдвигают нумерацию PK-шных.
        if hasattr(obj, "id") and getattr(obj, "id", None) is None:
            ids = [
                o.id
                for o in self.added
                if hasattr(o, "id") and isinstance(getattr(o, "id", None), int)
            ]
            obj.id = max(ids, default=0) + 1
        if hasattr(obj, "created_at") and getattr(obj, "created_at", None) is None:
            obj.created_at = datetime.now(timezone.utc)
        self.added.append(obj)

    async def get(self, model: Any, pk: Any, **_kwargs: Any) -> Any:
        # `**_kwargs` — populate_existing и прочие опции реальной сессии.
        # Ищем среди добавленных через add() (имитация flush в тестовой сессии)
        for obj in self.added:
            if obj.__class__ is model and (
                getattr(obj, "id", None) == pk
                or getattr(obj, "telegram_id", None) == pk
                or getattr(obj, "source_name", None) == pk
                or getattr(obj, "name", None) == pk  # Source.name и др. строковые PK
                or (
                    getattr(obj, "listing_id", None) is not None
                    and getattr(obj, "listing_id", None) == pk
                )  # InspectionReport и др. с unique listing_id
            ):
                return obj
        return None

    async def execute(self, stmt: Any, params: Any = None) -> Any:
        self.executed.append(stmt)
        if params is not None:
            self.executed_params.append(params)
        rows = self._row_queue.pop(0) if self._row_queue else self._rows

        class _Result:
            def scalars(self) -> "_Result":
                return self

            def all(self) -> list:
                return list(rows)

            def scalar(self) -> Any:
                # exists()/count(): одна колонка в первой строке.
                return rows[0] if rows else None

            def scalar_one_or_none(self) -> Any:
                return rows[0] if rows else None

            def first(self) -> Any:
                return rows[0] if rows else None

            def scalar_one(self) -> Any:
                # для count-запросов: число строк-результатов
                return len(rows)

            # UPDATE в mark_paid: фейк-сессия не знает строк, но платёж
            # в тестах pending — считаем «одна строка изменена».
            rowcount = 1

        return _Result()

    async def commit(self) -> None:
        self.committed = True

    async def flush(self) -> None:
        return None

    async def refresh(self, obj: Any) -> None:
        return None

    async def delete(self, obj: Any) -> None:
        self.deleted.append(obj)

    async def rollback(self) -> None:
        return None

    async def close(self) -> None:
        return None


@pytest.fixture
def fake_redis() -> FakeRedis:
    return FakeRedis()


@pytest.fixture(autouse=True)
def _deterministic_moderation_sampling(monkeypatch):
    """Сэмплинг партнёрской модерации (10 §2) — детерминизм тестов.

    10% партнёрских лотов уходят в ручную модерацию по random<0.1 —
    без фиксации любые API-тесты создания partner-лотов флапали бы.
    Здесь random() всегда вне выборки; тесты самого сэмплинга
    (test_listing_lifecycle) патчуют random локально поверх.
    """
    from app.application.market import moderation as _moderation

    class _NoSampling:
        @staticmethod
        def random() -> float:
            return 0.99

    monkeypatch.setattr(_moderation, "random", _NoSampling())


@pytest.fixture
def fake_session() -> FakeSession:
    return FakeSession()

# =========================================================================
# Инфраструктурные фикстуры (Postgres)
# =========================================================================


def _test_database_url() -> str:
    """DSN тестовой БД: TEST_DATABASE_URL или <database>_test.

    Отдельная БД обязательна: схема пересоздаётся на старте сессии.
    """
    url = os.getenv("TEST_DATABASE_URL")
    if url:
        return url
    base, _, _name = get_settings().database_url.rpartition("/")
    return f"{base}/autoselect_test"


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    """Явный backend для pytest-asyncio/anyio."""
    return "asyncio"


@pytest.fixture(scope="session")
async def engine():
    """Движок на тестовой БД. Схема создаётся один раз на сессию.

    Если Postgres недоступен — все зависимые тесты скипаются, а не падают
    каскадом «fixture not found».
    """
    engine = create_async_engine(_test_database_url(), poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)
    except Exception as exc:  # noqa: BLE001 — окружение без БД
        await engine.dispose()
        pytest.skip(f"test database unavailable: {exc}")
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
async def session(engine) -> AsyncIterator[AsyncSession]:
    """Сессия внутри внешней транзакции: rollback после каждого теста.

    `join_transaction_mode="create_savepoint"` — `commit()` внутри хендлера
    отпускает savepoint, но не фиксирует внешнюю транзакцию, поэтому данные
    теста не протекают в следующие.
    """
    async with engine.connect() as connection:
        transaction = await connection.begin()
        db_session = AsyncSession(
            bind=connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        try:
            yield db_session
        finally:
            await db_session.close()
            await transaction.rollback()


def install_test_container(
    app: object,
    session: AsyncSession | None = None,
    redis: FakeRedis | None = None,
    extra: tuple = (),
) -> AsyncContainer:
    """Подключить к приложению Dishka-контейнер на фейках БД/Redis.

    Сессия и Redis уходят под ключи `AsyncSession`/`Redis`, поэтому все
    сценарии (репозитории, use case-ы) собираются на тестовых двойниках.
    Аутентификация НЕ подменяется: положите `User` в сессию (`session.add`)
    и ходите с реальным Bearer-токеном (`create_access_token`); см.
    фикстуры `auth_headers*` ниже.

    Args:
        app: FastAPI-приложение.
        session: Сессия тестовой БД (иначе — `FakeSession`).
        redis: Фейковый Redis (иначе — `FakeRedis`).
        extra: Дополнительные dishka-провайдеры (перекрывают стандартные).

    Returns:
        Контейнер; вызывающий закрывает его `await container.close()`.
    """

    class _FakeInfra(Provider):
        """Подмена инфраструктуры: сессия — REQUEST, Redis — APP."""

        @provide(scope=Scope.REQUEST, provides=AsyncSession)
        def _session(self) -> AsyncSession:  # type: ignore[valid-type]
            return session if session is not None else FakeSession()  # type: ignore[return-value]

        @provide(scope=Scope.APP, provides=Redis)
        def _redis(self) -> Redis:  # type: ignore[valid-type]
            return redis if redis is not None else FakeRedis()  # type: ignore[return-value]

    container = make_container(_FakeInfra(), *extra)
    # Middleware dishka уже стоит на приложении (main_api/create_domain_service);
    # он читает контейнер из app.state на каждый запрос — достаточно подмены.
    app.state.dishka_container = container
    return container


@pytest.fixture
async def client(
    session: AsyncSession, fake_redis: FakeRedis
) -> AsyncIterator[AsyncClient]:
    """HTTP-клиент поверх ASGI: контейнер собран на тестовой сессии и `FakeRedis`.

    Redis — всегда `FakeRedis`: API-тесты не должны зависеть от живого Redis,
    а тест, запросивший `fake_redis`, получает тот же экземпляр (кеш фикстур
    в рамках одного теста).
    """
    previous_container = fastapi_app.state.dishka_container
    container = install_test_container(
        fastapi_app, session=session, redis=fake_redis
    )
    try:
        transport = ASGITransport(app=fastapi_app)
        async with AsyncClient(transport=transport, base_url="http://test") as http:
            yield http
    finally:
        fastapi_app.state.dishka_container = previous_container
        await container.close()


@pytest.fixture
async def http(client: AsyncClient) -> AsyncClient:
    """Алиас `client` — часть тестов использует имя `http`."""
    return client


@pytest.fixture
def internal_token() -> str:
    """Строковое значение internal-токена (SecretStr разворачивается)."""
    settings = get_settings()
    return (
        settings.internal_token.get_secret_value()
        or settings.app_secret_key.get_secret_value()
    )


@pytest.fixture
async def user(session: AsyncSession) -> User:
    """Активный покупатель, сохранённый в тестовой БД."""
    user = User(
        telegram_id=900001,
        username="fixture_user",
        role="buyer",
        subscription_tier="free",
        is_active=True,
        created_at=datetime.now(UTC),
    )
    session.add(user)
    await session.flush()
    return user


@pytest.fixture
def auth_headers(user: User) -> dict[str, str]:
    """Bearer-заголовок для `user`."""
    token = create_access_token(user.telegram_id, get_settings())
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
async def tg_client(client: AsyncClient, session: AsyncSession):
    """Пара (клиент, сессия) для сценариев логина, где нужны оба."""
    return client, session

@pytest.fixture
def auth_headers_user(user: User) -> dict[str, str]:
    """Bearer-заголовок того же пользователя, что и фикстура `user`."""
    token = create_access_token(user.telegram_id, get_settings())
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
async def auth_headers_admin(session: AsyncSession) -> dict[str, str]:
    """Bearer-заголовок администратора."""
    admin = User(
        telegram_id=900002,
        username="fixture_admin",
        role="admin",
        subscription_tier="business",
        is_active=True,
        created_at=datetime.now(UTC),
    )
    session.add(admin)
    await session.flush()
    token = create_access_token(admin.telegram_id, get_settings())
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
async def user_with_password(session: AsyncSession) -> User:
    """Пользователь с паролем: проверка, что хеш не утекает в ответ."""
    user = User(
        telegram_id=900003,
        username="fixture_pwd",
        role="buyer",
        subscription_tier="free",
        is_active=True,
        password_hash="pbkdf2_sha256$600000$salt$hash",
        created_at=datetime.now(UTC),
    )
    session.add(user)
    await session.flush()
    return user


@pytest.fixture
async def inactive_user(session: AsyncSession) -> User:
    """Деактивированный пользователь (бан)."""
    user = User(
        telegram_id=900004,
        username="fixture_banned",
        role="buyer",
        subscription_tier="free",
        is_active=False,
        created_at=datetime.now(UTC),
    )
    session.add(user)
    await session.flush()
    return user

# =========================================================================
# Query budget (контроль N+1)
# =========================================================================


@pytest.fixture
def query_counter(engine):
    """Считает SQL-запросы к тестовой БД через событие SQLAlchemy."""
    from sqlalchemy import event

    counter = {"n": 0}

    def _before(*args, **kwargs) -> None:
        counter["n"] += 1

    event.listen(engine.sync_engine, "before_cursor_execute", _before)
    try:
        yield counter
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", _before)


@pytest.fixture
def assert_max_queries(query_counter):
    """Проверка бюджета: `assert_max_queries(3)`."""

    def _assert(limit: int) -> None:
        actual = query_counter["n"]
        assert actual <= limit, f"query budget exceeded: {actual} > {limit}"

    return _assert


# =========================================================================
# Пользователи и данные для API-тестов
# =========================================================================


@pytest.fixture
async def free_user(session: AsyncSession) -> User:
    """Пользователь тарифа Free (проверки tier-bypass и квот)."""
    user = User(
        telegram_id=900010,
        username="fixture_free",
        role="buyer",
        subscription_tier="free",
        is_active=True,
        created_at=datetime.now(UTC),
    )
    session.add(user)
    await session.flush()
    return user


@pytest.fixture
async def pro_user(session: AsyncSession) -> User:
    """Пользователь с действующей подпиской Pro (`instant`, безлимит AI)."""
    user = User(
        telegram_id=900011,
        username="fixture_pro",
        role="buyer",
        subscription_tier="pro",
        subscription_until=datetime.now(UTC) + timedelta(days=30),
        is_active=True,
        created_at=datetime.now(UTC),
    )
    session.add(user)
    await session.flush()
    return user

@pytest.fixture
async def business_user(session: AsyncSession) -> User:
    """Пользователь с действующей подпиской Business (instant, агент, чеки)."""
    user = User(
        telegram_id=900012,
        username="fixture_business",
        role="buyer",
        subscription_tier="business",
        subscription_until=datetime.now(UTC) + timedelta(days=30),
        is_active=True,
        created_at=datetime.now(UTC),
    )
    session.add(user)
    await session.flush()
    return user


@pytest.fixture
def auth_headers_inactive_user(inactive_user: User) -> dict[str, str]:
    """Bearer-заголовок деактивированного пользователя (бан)."""
    token = create_access_token(inactive_user.telegram_id, get_settings())
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
async def guest_user(session: AsyncSession) -> User:
    """Гостевой аккаунт сайта: синтетический telegram_id < 0 и без email."""
    user = User(
        telegram_id=-900020,
        username="fixture_guest",
        role="buyer",
        subscription_tier="free",
        is_active=True,
        created_at=datetime.now(UTC),
    )
    session.add(user)
    await session.flush()
    return user


@pytest.fixture
def guest_headers(guest_user: User) -> dict[str, str]:
    """Bearer-заголовок гостевого аккаунта (`guest_user`)."""
    token = create_access_token(guest_user.telegram_id, get_settings())
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
async def listing(session: AsyncSession) -> Listing:
    """Активное объявление в БД — источник для лидов, метрик и AI-анализа."""
    listing = Listing(
        source="encar",
        source_id="fixture-1",
        url="https://example.test/encar/fixture-1",
        make="Kia",
        model="Carnival",
        year=2021,
        price=30000,
        currency="USD",
        mileage_km=60000,
        parsed_at=datetime.now(UTC),
    )
    session.add(listing)
    await session.flush()
    return listing


@pytest.fixture
async def lead(session: AsyncSession, user: User, listing: Listing) -> Lead:
    """Лид пользователя `user` по объявлению `listing` в статусе new."""
    lead = Lead(
        user_id=user.telegram_id,
        listing_id=listing.id,
        source="bot",
        status="new",
    )
    session.add(lead)
    await session.flush()
    return lead


@pytest.fixture
async def session_empty(session: AsyncSession) -> AsyncSession:
    """Сессия без включённых источников (таблица `sources` пуста)."""
    return session


@pytest.fixture
async def session_with_analysis(
    session: AsyncSession, listing: Listing
) -> AsyncSession:
    """Сессия с готовым AI-разбором листинга (кэш `GET .../analysis`)."""
    session.add(
        PriceAnalysis(
            listing_id=listing.id,
            user_id=0,
            verdict="fair",
            fair_min=29000,
            fair_max=33000,
            summary="Оценка по статистике рынка.",
            payload={
                "verdict": "fair",
                "price": float(listing.price),
                "currency": listing.currency,
                "fair_min": 29000.0,
                "fair_max": 33000.0,
                "summary": "Оценка по статистике рынка.",
                "factors": [],
                "comps": None,
                "history": [],
                "source": "ai",
            },
        )
    )
    await session.commit()
    return session


# =========================================================================
# Стаб-фабрика сессий для AdapterRegistry (unit-тесты без БД)
# =========================================================================


class FakeRowsResult:
    """Результат `execute()` для `FakeSessionFactory` (строки + scalar-аксессоры)."""

    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalars(self) -> "FakeRowsResult":
        return self

    def all(self) -> list[Any]:
        return list(self._rows)

    def scalar_one_or_none(self) -> Any:
        return self._rows[0] if self._rows else None

    def first(self) -> Any:
        return self._rows[0] if self._rows else None


class FakeRegistrySession:
    """Стаб-сессия AdapterRegistry: отдаёт строки фабрики с фильтром по `name`.

    Фильтр включается, когда в запросе есть строковый bind-параметр: так
    `select(...).where(Source.name == name)` в `reload_source` получает нужную
    строку, а `load()` (без параметров) — все.
    """

    def __init__(self, factory: "FakeSessionFactory") -> None:
        self._factory = factory

    async def __aenter__(self) -> "FakeRegistrySession":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    async def execute(self, stmt: Any) -> FakeRowsResult:
        rows = list(self._factory.rows)
        params = getattr(stmt.compile(), "params", {}) or {}
        names = {value for value in params.values() if isinstance(value, str)}
        if names:
            rows = [row for row in rows if getattr(row, "name", None) in names]
        return FakeRowsResult(rows)

    async def get(self, model: Any, pk: Any) -> Any:
        for row in self._factory.rows:
            if getattr(row, "name", None) == pk:
                return row
        return None


class FakeSessionFactory:
    """Фабрика стаб-сессий с изменяемым `rows` — для AdapterRegistry.

    Тест меняет `factory.rows` между `load()` / `reload_source()`, имитируя
    правки таблицы `sources` без реальной БД (тесты registry остаются unit).
    """

    def __init__(self, rows: list[Any] | None = None) -> None:
        self.rows: list[Any] = list(rows or [])

    def __call__(self) -> FakeRegistrySession:
        return FakeRegistrySession(self)


@pytest.fixture
def fake_session_factory() -> FakeSessionFactory:
    """Пустая фабрика сессий `sources`; наполняется в тесте через `.rows`."""
    return FakeSessionFactory()
