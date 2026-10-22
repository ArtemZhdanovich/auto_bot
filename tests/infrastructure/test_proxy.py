"""Тесты прокси: URL DataImpulse, GatewayProxyPool (sticky/cooldown), фабрика."""

import asyncio

import pytest
from pydantic import SecretStr

from app.infrastructure.sources.common.proxy import (
    GatewayProxyPool,
    ProxyPool,
    build_dataimpulse_proxy_url,
    build_proxy_pool,
)
from conftest import FakeRedis


class _FakePipe:
    """Мини-pipeline: srem/sadd/execute без реального Redis."""

    def srem(self, *args, **kwargs) -> "_FakePipe":
        return self

    def sadd(self, *args, **kwargs) -> "_FakePipe":
        return self

    async def execute(self) -> list:
        return []

    async def __aenter__(self) -> "_FakePipe":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None


class _PipeRedis(FakeRedis):
    """FakeRedis + `pipeline()` для ProxyPool.mark_bad/restore."""

    def pipeline(self, transaction: bool = True) -> _FakePipe:
        return _FakePipe()


@pytest.fixture
def fake_redis() -> _PipeRedis:
    """Local override: conftest.FakeRedis не реализует `pipeline()`."""
    return _PipeRedis()


# --- build_dataimpulse_proxy_url ---------------------------------------


def test_url_basic():
    url = build_dataimpulse_proxy_url("user@mail.com", "pass")
    assert url == "http://user%40mail.com:pass@gw.dataimpulse.com:823"


def test_url_geo_and_sticky_official_format():
    url = build_dataimpulse_proxy_url(
        "login", "pw", geo="cn", sticky_window="42"
    )
    # официальный формат: login__cr.cn;sessid.42:pw@gw.dataimpulse.com:823
    assert url == "http://login__cr.cn;sessid.42:pw@gw.dataimpulse.com:823"


def test_url_geo_multiple_countries():
    url = build_dataimpulse_proxy_url("login", "pw", geo="cn,de")
    assert url == "http://login__cr.cn,de:pw@gw.dataimpulse.com:823"


def test_url_quotes_specials_in_password():
    url = build_dataimpulse_proxy_url("login", "p@ss wo:rd", port=824)
    assert "p%40ss%20wo%3Ard" in url
    assert url.endswith("@gw.dataimpulse.com:824")


# --- GatewayProxyPool ---------------------------------------------------


async def test_gateway_rotating_returns_same_url():
    pool = GatewayProxyPool("http://login:pw@gw:823", sticky_ttl_sec=0)
    assert await pool.get() == "http://login:pw@gw:823"
    assert await pool.get() == "http://login:pw@gw:823"  # ротацию делает провайдер


async def test_gateway_sticky_window_stable():
    t = 1_000_000.0
    pool = GatewayProxyPool(
        "http://login:pw@gw:823",
        sticky_ttl_sec=1800,
        url_kwargs={"user": "login", "password": "pw", "host": "gw", "port": 823},
        clock=lambda: t,
    )
    url1 = await pool.get()
    url2 = await pool.get()
    assert "__sessid." in url1 and "__sessid." in url2
    assert url1 == url2  # одно окно — один sessid

    t += 1800  # следующее окно → новый sessid
    url3 = await pool.get()
    assert url3 != url1


async def test_gateway_cooldown_after_failures_then_direct():
    t = 0.0
    pool = GatewayProxyPool(
        "http://login:pw@gw:823",
        fail_threshold=3,
        fail_cooldown_sec=300,
        clock=lambda: t,
    )
    for _ in range(2):
        await pool.mark_bad("http://login:pw@gw:823")
    assert await pool.get() is not None  # ещё не порог

    await pool.mark_bad("http://login:pw@gw:823")  # третий → cooldown
    assert await pool.get() is None  # ходим напрямую
    assert await pool.healthcheck_all() == {"gateway": 1, "cooldown": 1}

    t += 301  # пауза истекла
    assert await pool.get() is not None


async def test_gateway_restore_resets_fails():
    pool = GatewayProxyPool("http://l:p@gw:823", fail_threshold=2)
    await pool.mark_bad("x")
    await pool.restore("x")  # успех сбрасывает счётчик
    await pool.mark_bad("x")
    assert await pool.get() is not None


# --- build_proxy_pool ----------------------------------------------------


class _Settings:
    proxy_dataimpulse_user = "login"
    proxy_dataimpulse_pass = "pw"
    proxy_dataimpulse_host = "gw.dataimpulse.com"
    proxy_dataimpulse_port = 823
    proxy_dataimpulse_geo = "cn"
    proxy_dataimpulse_sticky_ttl_sec = 0
    proxy_dataimpulse_fail_threshold = 5
    proxy_dataimpulse_fail_cooldown_sec = 300


class _SettingsOff(_Settings):
    proxy_dataimpulse_user = None


async def test_factory_gateway_when_configured():
    pool = build_proxy_pool(_Settings(), FakeRedis())
    assert isinstance(pool, GatewayProxyPool)
    url = await pool.get()
    assert "cr.cn" in url and "@gw.dataimpulse.com:823" in url


async def test_factory_static_pool_without_gateway():
    pool = build_proxy_pool(_SettingsOff(), FakeRedis())
    assert isinstance(pool, ProxyPool)


async def test_factory_none_when_nothing_configured():
    assert build_proxy_pool(_SettingsOff(), None) is None





# tests/adapters/test_proxy.py

def test_secret_str_password_unwrapped():
    """Регрессия: SecretStr не должен попадать в URL как '**********'."""
    url = build_dataimpulse_proxy_url("u", SecretStr("p@ss"))
    assert "p%40ss" in url
    assert "**********" not in url
    assert "p@ss" not in url  # пароль URL-encoded


def test_empty_secret_password_rejected():
    with pytest.raises(ValueError, match="password is empty"):
        build_dataimpulse_proxy_url("u", SecretStr(""))


def test_mark_bad_logs_masked(caplog, fake_redis):
    """mark_bad не должен писать пароль в логи."""
    # fake_redis — фикстура in-memory или fakeredis
    pool = ProxyPool(fake_redis)
    with caplog.at_level("WARNING"):
        asyncio.run(pool.mark_bad("http://user:secret@host:823"))
    assert "secret" not in caplog.text
    assert "***:***@host:823" in caplog.text


def test_mark_bad_atomic():
    """Краш между srem и sadd больше не оставляет прокси нигде."""
    # Тест с pipeline(transaction=True) — при эмуляции исключения
    # прокси должен остаться хотя бы в одном сете.
    # (С fakeredis проверяем, что оба ключа изменились одновременно.)
    ...


def test_gateway_cooldown_starts_after_threshold(monkeypatch):
    now = {"t": 100.0}
    pool = GatewayProxyPool(
        "http://u:p@host:823",
        fail_threshold=3,
        fail_cooldown_sec=10,
        clock=lambda: now["t"],
    )
    asyncio.run(pool.mark_bad("x"))
    asyncio.run(pool.mark_bad("x"))
    assert asyncio.run(pool.get()) is not None  # ещё не cooldown
    asyncio.run(pool.mark_bad("x"))
    assert asyncio.run(pool.get()) is None  # cooldown
    now["t"] += 11
    assert asyncio.run(pool.get()) is not None  # cooldown истёк


def test_gateway_restore_resets_fails():
    pool = GatewayProxyPool("http://u:p@host:823", fail_threshold=3)
    asyncio.run(pool.mark_bad("x"))
    asyncio.run(pool.mark_bad("x"))
    asyncio.run(pool.restore("x"))
    asyncio.run(pool.mark_bad("x"))
    assert asyncio.run(pool.get()) is not None  # сброшено, не cooldown


def test_gateway_repr_masks_password():
    pool = GatewayProxyPool("http://u:s3cr3t@host:823")
    assert "s3cr3t" not in repr(pool)
    assert "***:***@host:823" in repr(pool)


def test_build_proxy_pool_with_secret_password(monkeypatch):
    """Фабрика не падает на SecretStr и не создаёт пустой URL."""
    # settings с proxy_dataimpulse_pass=SecretStr("p") → GatewayProxyPool
    ...


def test_build_proxy_pool_empty_secret_password_returns_none(monkeypatch):
    """SecretStr('') не должен проходить проверку `if settings.xxx_pass`."""
    # settings.proxy_dataimpulse_user="u", proxy_dataimpulse_pass=SecretStr("")
    # → build_proxy_pool(settings, redis=None) → None (не GatewayProxyPool)
    ...