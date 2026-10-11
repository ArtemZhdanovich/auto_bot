"""Тесты прокси: URL DataImpulse, GatewayProxyPool (sticky/cooldown), фабрика."""

from app.adapters.common.proxy import (
    GatewayProxyPool,
    ProxyPool,
    build_dataimpulse_proxy_url,
    build_proxy_pool,
)
from conftest import FakeRedis


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
