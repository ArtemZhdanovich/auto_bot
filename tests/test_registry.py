"""Тесты AdapterRegistry (ТЗ п. 6.5)."""

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.infrastructure.sources.common.http import HttpClient
from app.infrastructure.sources.encar import EncarApiAdapter
from app.infrastructure.sources.registry import AdapterRegistry, EnvSecretStore, SecretError


class FakeSecretStore:
    """Хранилище секретов в памяти: ref -> dict (как отдаёт EnvSecretStore)."""

    def __init__(self, secrets: dict[str, dict[str, Any]] | None = None) -> None:
        self.secrets = secrets or {}

    async def get(self, ref: str) -> dict[str, Any] | None:
        return self.secrets.get(ref)


def _source_row(
    name: str = "encar", adapter_class: str = "EncarApiAdapter", **kw: Any
) -> Any:
    """Строка таблицы `sources` (SimpleNamespace вместо ORM)."""
    defaults = dict(
        name=name,
        adapter_class=adapter_class,
        kind="api",
        enabled=True,
        priority=100,
        rate_limit_per_min=60,
        timeout_sec=30,
        config={"page_size": 5},
        credentials_ref=None,
        capabilities={"country": "KR"},
    )
    defaults.update(kw)
    return SimpleNamespace(**defaults)


def _track_close(adapter: Any) -> AsyncMock:
    """Подменяет `close()` адаптера шпионом и возвращает его."""
    spy = AsyncMock()
    adapter.close = spy
    return spy


# --- load -----------------------------------------------------------


@pytest.mark.asyncio
async def test_registry_loads_enabled_sources(fake_session_factory):
    fake_session_factory.rows = [_source_row()]
    registry = AdapterRegistry(fake_session_factory)
    await registry.load()

    adapter = registry.get("encar")
    assert isinstance(adapter, EncarApiAdapter)
    assert adapter.source_name == "encar"
    assert adapter.config == {"page_size": 5}
    assert adapter.capabilities == {"country": "KR"}
    assert registry.all() == [adapter]
    await registry.aclose()


@pytest.mark.asyncio
async def test_registry_skips_unknown_adapter_class(fake_session_factory):
    fake_session_factory.rows = [
        _source_row(),
        _source_row(name="ghost", adapter_class="NopeAdapter"),
    ]
    registry = AdapterRegistry(fake_session_factory)
    await registry.load()

    assert registry.all() == [registry.get("encar")]
    await registry.aclose()


@pytest.mark.asyncio
async def test_registry_resolves_credentials_ref(fake_session_factory):
    fake_session_factory.rows = [_source_row(credentials_ref="ENCAR_API_KEY")]
    registry = AdapterRegistry(
        fake_session_factory,
        secret_store=FakeSecretStore({"ENCAR_API_KEY": {"api_key": "secret-1"}}),
    )
    await registry.load()
    assert registry.get("encar").credentials == {"api_key": "secret-1"}
    await registry.aclose()


@pytest.mark.asyncio
async def test_registry_get_unknown_raises(fake_session_factory):
    registry = AdapterRegistry(fake_session_factory)
    await registry.load()
    with pytest.raises(KeyError):
        registry.get("nope")


@pytest.mark.asyncio
async def test_registry_reload_replaces_cache(fake_session_factory):
    fake_session_factory.rows = [_source_row()]
    registry = AdapterRegistry(fake_session_factory)
    await registry.load()
    first = registry.get("encar")

    await registry.reload()
    second = registry.get("encar")
    assert first is not second
    # reload собрал новые клиенты, они рабочие
    assert isinstance(first.http, HttpClient)
    assert isinstance(second.http, HttpClient)
    await registry.aclose()


@pytest.mark.asyncio
async def test_registry_proxy_pool_only_for_use_proxy_sources(fake_session_factory):
    """Шлюз достаётся только источникам с capabilities.use_proxy=true."""

    class FakePool:
        pass

    fake_session_factory.rows = [
        _source_row(
            name="che168",
            adapter_class="Che168HtmlAdapter",
            capabilities={"use_proxy": True},
        ),
        _source_row(name="encar", capabilities={"country": "KR"}),  # без флага
    ]
    registry = AdapterRegistry(fake_session_factory, proxy_pool=FakePool())
    await registry.load()

    che = registry.get("che168")
    assert che.http._proxy_pool is not None  # ходит через шлюз

    enc = registry.get("encar")
    assert enc.http._proxy_pool is None  # платный трафик не жжёт
    await registry.aclose()


@pytest.mark.asyncio
async def test_registry_reload_source_picks_up_new_config(fake_session_factory):
    row = _source_row(
        name="che168",
        adapter_class="Che168HtmlAdapter",
        kind="parser",
        config={"card_selector": "li.old"},
    )
    fake_session_factory.rows = [row]
    registry = AdapterRegistry(fake_session_factory)
    await registry.load()

    row.config = {"card_selector": "div.new"}  # «врач» обновил конфиг в БД
    adapter = await registry.reload_source("che168")
    assert adapter is registry.get("che168")
    assert adapter.config["card_selector"] == "div.new"
    await registry.aclose()


@pytest.mark.asyncio
async def test_load_keeps_old_cache_until_rebuild_done(fake_session_factory):
    """Пока load() строится, get() возвращает старые адаптеры."""
    fake_session_factory.rows = [_source_row()]
    registry = AdapterRegistry(fake_session_factory)
    await registry.load()
    old = registry.get("encar")

    started = asyncio.Event()
    release = asyncio.Event()
    original_build = registry._build

    async def slow_build(row: Any) -> Any:
        started.set()
        await release.wait()
        return await original_build(row)

    registry._build = slow_build  # type: ignore[method-assign]
    task = asyncio.create_task(registry.load())
    await started.wait()
    assert registry.get("encar") is old  # старый кеш не тронут
    release.set()
    await task

    assert registry.get("encar") is not old
    await registry.aclose()


@pytest.mark.asyncio
async def test_load_closes_removed_adapters(fake_session_factory):
    """Источник, пропавший из БД, закрывается."""
    fake_session_factory.rows = [
        _source_row(),
        _source_row(name="kcar", adapter_class="KCarApiAdapter"),
    ]
    registry = AdapterRegistry(fake_session_factory)
    await registry.load()
    a_encar = registry.get("encar")
    close_spy = _track_close(a_encar)

    fake_session_factory.rows = [
        _source_row(name="kcar", adapter_class="KCarApiAdapter")
    ]
    await registry.load()

    with pytest.raises(KeyError):
        registry.get("encar")
    close_spy.assert_awaited_once()
    await registry.aclose()


@pytest.mark.asyncio
async def test_load_survives_one_bad_source(fake_session_factory):
    """Один битый адаптер не роняет load, остальные загружены."""
    fake_session_factory.rows = [
        _source_row(),
        _source_row(name="kcar", adapter_class="KCarApiAdapter"),
        _source_row(name="ghost", adapter_class="NopeAdapter"),
    ]
    registry = AdapterRegistry(fake_session_factory)
    await registry.load()
    assert len(registry.all()) == 2  # два выжили
    await registry.aclose()


# --- reload_source --------------------------------------------------


@pytest.mark.asyncio
async def test_reload_source_closes_old(fake_session_factory):
    fake_session_factory.rows = [_source_row()]
    registry = AdapterRegistry(fake_session_factory)
    await registry.load()
    old = registry.get("encar")
    close_spy = _track_close(old)

    await registry.reload_source("encar")
    close_spy.assert_awaited_once()
    assert registry.get("encar") is not old
    await registry.aclose()


@pytest.mark.asyncio
async def test_reload_source_disabled_closes_and_pops(fake_session_factory):
    row = _source_row()
    fake_session_factory.rows = [row]
    registry = AdapterRegistry(fake_session_factory)
    await registry.load()
    old = registry.get("encar")
    close_spy = _track_close(old)

    row.enabled = False  # в БД encar выключили
    result = await registry.reload_source("encar")
    assert result is None
    with pytest.raises(KeyError):
        registry.get("encar")
    close_spy.assert_awaited_once()


# --- _build: валидации ---------------------------------------------


@pytest.mark.asyncio
async def test_build_use_proxy_string_false_is_falsey(fake_session_factory):
    """`use_proxy: "false"` (строка) не должен включать прокси."""
    registry = AdapterRegistry(fake_session_factory, proxy_pool=object())
    adapter = await registry._build(
        _source_row(capabilities={"use_proxy": "false"})
    )
    assert adapter.http._proxy_pool is None


@pytest.mark.asyncio
async def test_build_missing_secret_raises(fake_session_factory, monkeypatch):
    """credentials_ref задан, env отсутствует → SecretError."""
    monkeypatch.delenv("ADAPTER_MISSING", raising=False)
    registry = AdapterRegistry(fake_session_factory)
    with pytest.raises(SecretError, match="not found"):
        await registry._build(_source_row(credentials_ref="ADAPTER_MISSING"))


@pytest.mark.asyncio
async def test_build_none_timeout_uses_default(fake_session_factory):
    registry = AdapterRegistry(fake_session_factory)
    adapter = await registry._build(_source_row(timeout_sec=None))
    assert adapter.timeout == 30


@pytest.mark.asyncio
async def test_build_config_string_does_not_crash(fake_session_factory):
    """row.config = "{}" (строка) → {} + warning, не AttributeError."""
    registry = AdapterRegistry(fake_session_factory)
    adapter = await registry._build(_source_row(config="{}"))
    assert adapter.config == {}


# --- aclose ---------------------------------------------------------


@pytest.mark.asyncio
async def test_aclose_closes_all(fake_session_factory):
    fake_session_factory.rows = [
        _source_row(),
        _source_row(name="kcar", adapter_class="KCarApiAdapter"),
    ]
    registry = AdapterRegistry(fake_session_factory)
    await registry.load()
    adapters = registry.all()
    spies = [_track_close(adapter) for adapter in adapters]

    await registry.aclose()
    assert all(spy.await_count == 1 for spy in spies)
    assert registry.all() == []


@pytest.mark.asyncio
async def test_aclose_idempotent(fake_session_factory):
    fake_session_factory.rows = [_source_row()]
    registry = AdapterRegistry(fake_session_factory)
    await registry.load()
    await registry.aclose()
    await registry.aclose()  # не падает


# --- EnvSecretStore -------------------------------------------------


@pytest.mark.asyncio
async def test_env_store_parses_json(monkeypatch):
    monkeypatch.setenv("ADAPTER_TEST", '{"api_key": "abc"}')
    store = EnvSecretStore()
    assert await store.get("ADAPTER_TEST") == {"api_key": "abc"}


@pytest.mark.asyncio
async def test_env_store_wraps_plain_token(monkeypatch):
    monkeypatch.setenv("ADAPTER_TEST", "just-a-token")
    store = EnvSecretStore()
    assert await store.get("ADAPTER_TEST") == {"token": "just-a-token"}


@pytest.mark.asyncio
async def test_env_store_missing_returns_none(monkeypatch):
    monkeypatch.delenv("ADAPTER_TEST", raising=False)
    store = EnvSecretStore()
    assert await store.get("ADAPTER_TEST") is None


@pytest.mark.asyncio
async def test_env_store_rejects_system_env(monkeypatch):
    """credentials_ref не должен читать системные переменные."""
    store = EnvSecretStore()
    with pytest.raises(SecretError, match="must start with one of"):
        await store.get("PATH")  # системная env
    with pytest.raises(SecretError, match="must start with one of"):
        await store.get("AWS_SECRET_ACCESS_KEY")


@pytest.mark.asyncio
async def test_env_store_rejects_non_dict_json(monkeypatch):
    monkeypatch.setenv("ADAPTER_TEST", '"just-a-string"')
    store = EnvSecretStore()
    with pytest.raises(SecretError, match="must be a JSON object"):
        await store.get("ADAPTER_TEST")
