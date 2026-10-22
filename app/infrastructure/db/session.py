"""Подключение к PostgreSQL (async engine + сессии).

`engine` создаётся один раз при импорте и держит пул соединений
(`AsyncEngine`). Для graceful shutdown обязателен `await dispose_engine()`
в lifespan-handler (`app/main.py`) — иначе idle-соединения копятся и
упираются в `max_connections` Postgres.

Pool-параметры (per-worker):
- `pool_size=10`, `max_overflow=20` — до 30 соединений на воркер.
  При `--workers 4` = 120; для Postgres `max_connections=100` это
  много — уменьшить `pool_size` или `max_overflow` под инстанс.
- `pool_recycle=1800` — пересоздавать соединения раз в 30 мин
  (idle-таймаут Postgres/PgBouncer часто 10–30 мин).
- `pool_pre_ping=True` — safety net (round-trip `SELECT 1` на checkout).
- `pool_timeout=10` — быстрый fail при исчерпании пула.
- `pool_use_lifo=True` — меньше idle-соединений в ночное время.

`connect_args.server_settings` задаёт серверные таймауты:
- `statement_timeout=30s` — runaway-запрос не держит пул.
- `lock_timeout=5s` — ожидание блокировки не бесконечно.
- `idle_in_transaction_session_timeout=60s` — «зависшая» транзакция
  абортится сервером.
- `application_name` — видно в `pg_stat_activity`.

`expire_on_commit=False` — объекты НЕ refresh'атся после `commit()`.
Серверные `server_default` (`func.now()`, `'[]'::jsonb`) не появятся
в Python-объекте — нужен явный `await session.refresh(obj)`. Это
осознанный tradeoff (производительность), задокументирован здесь.
"""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import get_settings

settings = get_settings()


def _build_engine() -> AsyncEngine:
    """Собирает `AsyncEngine` с прод-параметрами пула и серверными таймаутами.

    Returns:
        Настроенный `AsyncEngine`.
    """
    return create_async_engine(
        settings.database_url,
        pool_pre_ping=True,
        pool_size=getattr(settings, "db_pool_size", 10),
        max_overflow=getattr(settings, "db_max_overflow", 20),
        pool_timeout=getattr(settings, "db_pool_timeout_sec", 10),
        pool_recycle=getattr(settings, "db_pool_recycle_sec", 1800),
        pool_use_lifo=True,
        # `command_timeout` — на уровне клиента (asyncpg),
        # `server_settings` — на уровне сервера (Postgres).
        connect_args={
            "timeout": 10,  # TCP-connect timeout (OS default 75s — слишком много)
            "command_timeout": 30,  # client-side safety net
            "server_settings": {
                "application_name": "autoselect-api",
                "statement_timeout": "30000",
                "lock_timeout": "5000",
                "idle_in_transaction_session_timeout": "60000",
            },
        },
    )


engine: AsyncEngine = _build_engine()

session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI-зависимость: сессия БД на время запроса.

    `async with session_factory()` на выходе вызывает `session.close()`,
    который делает rollback незакоммиченной транзакции. Явный
    `rollback()` в `except` добавлен для читаемости и для случая
    middleware, оборачивающего endpoint.

    Yields:
        `AsyncSession` с автобиндом (транзакция открывается на первом
        query, коммитится явным `await session.commit()`).

    Example:
        ```python
        @router.post("/items")
        async def create_item(session: SessionDep):
            session.add(Item(...))
            await session.commit()  # явный commit
            await session.refresh(item)  # server_default → Python
    """
    async with session_factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise

async def warmup_engine() -> None:
    """Прогревает пул одним соединением на старте приложения.

    Без этого первые N запросов после старта воркера открывают
    соединения (TCP + TLS + auth) — «cold start» 500-е при burst.
    """
    async with engine.connect() as conn:
        await conn.exec_driver_sql("SELECT 1")

async def dispose_engine() -> None:
    """Закрывает пул соединений. Вызывается из lifespan на shutdown."""
    await engine.dispose()
