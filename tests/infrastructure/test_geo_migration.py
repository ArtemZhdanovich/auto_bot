"""Миграция 0031_geo_listing_columns: полный прогон alembic-цепочки.

Накат `upgrade head` на чистую scratch-БД (отдельная база на том же
сервере, что и тестовая): проверяем, что цепочка 0001→0031 накатывается
без ошибок, колонки country/city и индекс появляются, версия = 0031;
откат `downgrade 0030` убирает колонки и индекс. БД создаётся и
сносится тестом, тестовая база autoselect_test не затрагивается.

Команды alembic выполняются через `asyncio.to_thread`: env.py зовёт
`asyncio.run()`, который нельзя вызвать в уже работающем цикле pytest.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.conftest import _test_database_url

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = PROJECT_ROOT / "alembic.ini"
SCRATCH_DB = "autoselect_geo_migration_test"


def _scratch_url() -> str:
    """DSN scratch-БД: та же сервер/учётка, имя базы — SCRATCH_DB."""
    parts = urlsplit(_test_database_url())
    return parts._replace(path=f"/{SCRATCH_DB}").geturl()


async def _admin(action: str) -> None:
    """CREATE/DROP DATABASE scratch через maintenance-базу postgres."""
    import asyncpg

    parts = urlsplit(_test_database_url())
    conn = await asyncpg.connect(
        host=parts.hostname,
        port=parts.port or 5432,
        user=parts.username,
        password=parts.password,
        database="postgres",
    )
    try:
        await conn.execute(action)
    finally:
        await conn.close()


@pytest.fixture
def alembic_config(monkeypatch) -> Config:
    """Конфиг alembic, направленный в scratch-БД.

    env.py берёт URL из `get_settings().database_url` — подменяем
    `app.config.get_settings` стабом со ссылкой на scratch-БД.
    """
    url = _scratch_url()
    monkeypatch.setattr(
        "app.config.get_settings", lambda: SimpleNamespace(database_url=url)
    )
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option(
        "script_location", str(PROJECT_ROOT / "app" / "infrastructure" / "db" / "migrations")
    )
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


async def _column_names(engine) -> set[str]:
    async with engine.connect() as conn:
        rows = await conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'listings'"
            )
        )
        return {row[0] for row in rows}


async def _index_names(engine) -> set[str]:
    async with engine.connect() as conn:
        rows = await conn.execute(
            text(
                "SELECT indexname FROM pg_indexes WHERE tablename = 'listings'"
            )
        )
        return {row[0] for row in rows}


@pytest.mark.asyncio
async def test_geo_migration_upgrade_downgrade(engine, alembic_config):  # noqa: ARG001
    """Цепочка до head: country/city + индекс; откат до 0030 убирает их."""
    # `engine` — маркер доступности Postgres (иначе тест скипается).
    try:
        await _admin(f'DROP DATABASE IF EXISTS "{SCRATCH_DB}" (FORCE)')
    except Exception:  # noqa: BLE001 — старые PG без FORCE
        await _admin(f'DROP DATABASE IF EXISTS "{SCRATCH_DB}"')
    await _admin(f'CREATE DATABASE "{SCRATCH_DB}"')

    scratch = create_async_engine(_scratch_url())
    try:
        await asyncio.to_thread(command.upgrade, alembic_config, "head")

        columns = await _column_names(scratch)
        assert "country" in columns
        assert "city" in columns
        assert "ix_listings_country" in await _index_names(scratch)

        async with scratch.connect() as conn:
            version = await conn.execute(text("SELECT version_num FROM alembic_version"))
            assert version.scalar_one() == "0031"

        await asyncio.to_thread(command.downgrade, alembic_config, "0030")
        columns = await _column_names(scratch)
        assert "country" not in columns
        assert "city" not in columns
        assert "ix_listings_country" not in await _index_names(scratch)
    finally:
        await scratch.dispose()
        await _admin(f'DROP DATABASE IF EXISTS "{SCRATCH_DB}"')


@pytest.mark.asyncio
async def test_geo_migration_backfills_source_map(engine, alembic_config):  # noqa: ARG001
    """Бэкфилл §2: encar/kcar→KR, che168→CN, dubizzle→AE, cars_com→US,
    kolesa→KZ; неизвестные источники и market не трогаются."""
    try:
        await _admin(f'DROP DATABASE IF EXISTS "{SCRATCH_DB}" (FORCE)')
    except Exception:  # noqa: BLE001 — старые PG без FORCE
        await _admin(f'DROP DATABASE IF EXISTS "{SCRATCH_DB}"')
    await _admin(f'CREATE DATABASE "{SCRATCH_DB}"')

    scratch = create_async_engine(_scratch_url())
    try:
        await asyncio.to_thread(command.upgrade, alembic_config, "0030")
        # Сид ДО миграции 0031: по строке на источник мапы + чужие.
        seed = """
        INSERT INTO listings (source, source_id, url, parsed_at)
        VALUES
            ('encar', 's-encar', 'https://encar.test/1', now()),
            ('kcar', 's-kcar', 'https://kcar.test/1', now()),
            ('che168', 's-che', 'https://che168.test/1', now()),
            ('dubizzle', 's-dub', 'https://dubizzle.test/1', now()),
            ('cars_com', 's-carscom', 'https://cars.test/1', now()),
            ('kolesa', 's-kolesa', 'https://kolesa.test/1', now()),
            ('mobile_de', 's-mobile', 'https://mobile.test/1', now()),
            ('market', 's-market', '/listing/market/s-market', now())
        """
        async with scratch.begin() as conn:
            await conn.execute(text(seed))

        # Сама 0031: колонки + бэкфилл по source-мапе.
        await asyncio.to_thread(command.upgrade, alembic_config, "0031")

        async with scratch.connect() as conn:
            rows = await conn.execute(
                text("SELECT source, country FROM listings ORDER BY source")
            )
            by_source = dict(rows.all())
        assert by_source["encar"] == "KR"
        assert by_source["kcar"] == "KR"
        assert by_source["che168"] == "CN"
        assert by_source["dubizzle"] == "AE"
        assert by_source["cars_com"] == "US"
        assert by_source["kolesa"] == "KZ"
        # Неизвестные источники и рынок — без страны (NULL).
        assert by_source["mobile_de"] is None
        assert by_source["market"] is None
    finally:
        await scratch.dispose()
        await _admin(f'DROP DATABASE IF EXISTS "{SCRATCH_DB}"')
