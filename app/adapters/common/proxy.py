"""Пул резидентских прокси в Redis (ТЗ п. 6.3).

Ключи: `proxy:good`, `proxy:bad`, `proxy:cooldown`.
"""

import logging
import time
from typing import Any

from redis.asyncio import Redis

logger = logging.getLogger(__name__)

GOOD_KEY = "proxy:good"
BAD_KEY = "proxy:bad"
COOLDOWN_KEY = "proxy:cooldown"


class ProxyPool:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def get(self) -> str | None:
        """Случайный рабочий прокси или None (прямой запрос), если пул пуст."""
        proxy = await self._redis.srandmember(GOOD_KEY)
        return proxy.decode() if isinstance(proxy, bytes) else proxy

    async def add(self, *proxies: str) -> None:
        if proxies:
            await self._redis.sadd(GOOD_KEY, *proxies)

    async def mark_bad(self, proxy: str) -> None:
        await self._redis.srem(GOOD_KEY, proxy)
        await self._redis.sadd(BAD_KEY, proxy)
        logger.warning("proxy_marked_bad: %s", proxy)

    async def restore(self, proxy: str) -> None:
        """Вернуть прокси из bad/cooldown в good после успешного healthcheck."""
        await self._redis.srem(BAD_KEY, proxy)
        await self._redis.srem(COOLDOWN_KEY, proxy)
        await self._redis.sadd(GOOD_KEY, proxy)

    async def healthcheck_all(self) -> dict[str, int]:
        """Поддерживающая проверка: считает прокси по группам.

        Полноценный healthcheck выполняется Scheduler'ом (этап 8).
        """
        return {
            "good": await self._redis.scard(GOOD_KEY),
            "bad": await self._redis.scard(BAD_KEY),
            "cooldown": await self._redis.scard(COOLDOWN_KEY),
        }


def build_dataimpulse_proxy_url(
    user: str,
    password: str,
    host: str = "gw.dataimpulse.com",
    port: int = 823,
    geo: str | None = None,
    sticky_window: str | None = None,
) -> str:
    """URL шлюза DataImpulse по официальному формату доков.

    Параметры дописываются к логину после "__", разделяются ";",
    key.value: cr.cn (страна), sessid.X (sticky ~30 мин). Пример:
    http://login__cr.cn;sessid.42:pass@gw.dataimpulse.com:823
    """
    from urllib.parse import quote

    username = quote(user, safe="")
    params: list[str] = []
    if geo:
        params.append(f"cr.{geo}")
    if sticky_window:
        params.append(f"sessid.{sticky_window}")
    if params:
        username += "__" + ";".join(params)
    return f"http://{username}:{quote(password, safe='')}@{host}:{port}"


class GatewayProxyPool:
    """Пул из одного ротационного шлюза (DataImpulse и совместимые).

    Отличия от ProxyPool (статические IP):
    - ротацию IP делает провайдер (per request или sessid-окно) —
      нам не нужно хранить список IP и bad-статусы;
    - mark_bad() не выбрасывает шлюз навсегда: только N ошибок подряд
      (ConnectError и т.п.) усыпляют его на cooldown — после этого
      get() возвращает None и HttpClient ходит напрямую, пока пауза
      не истечёт. Так обрыв шлюза не убивает парсинг целиком.
    """

    def __init__(
        self,
        url: str,
        *,
        sticky_ttl_sec: int = 0,
        fail_threshold: int = 5,
        fail_cooldown_sec: int = 300,
        url_kwargs: dict[str, Any] | None = None,
        clock: Any | None = None,
    ) -> None:
        self._url = url
        self._url_kwargs = url_kwargs or {}
        self._sticky_ttl = sticky_ttl_sec
        self._fail_threshold = fail_threshold
        self._fail_cooldown_sec = fail_cooldown_sec
        self._clock = clock or time.monotonic
        self._fails = 0
        self._cooldown_until = 0.0

    def _sticky_window(self) -> str | None:
        """Стабильный sessid в пределах окна — детерминированно, без состояния."""
        if self._sticky_ttl <= 0:
            return None  # ротация на каждый запрос (default провайдера)
        return str(int(self._clock() // self._sticky_ttl))

    async def get(self) -> str | None:
        if self._clock() < self._cooldown_until:
            return None  # шлюз отдыхает — прямой запрос
        if self._sticky_ttl <= 0:
            return self._url
        return build_dataimpulse_proxy_url(
            **self._url_kwargs, sticky_window=self._sticky_window()
        )

    async def mark_bad(self, proxy: str) -> None:
        self._fails += 1
        if self._fails >= self._fail_threshold:
            self._cooldown_until = self._clock() + self._fail_cooldown_sec
            self._fails = 0
            logger.warning(
                "gateway_proxy_cooldown: %s sec (fails)", self._fail_cooldown_sec
            )

    async def restore(self, proxy: str) -> None:
        self._fails = 0

    async def healthcheck_all(self) -> dict[str, int]:
        on_cooldown = 1 if self._clock() < self._cooldown_until else 0
        return {"gateway": 1, "cooldown": on_cooldown}


def build_proxy_pool(settings: Any, redis: Redis | None) -> ProxyPool | GatewayProxyPool | None:
    """Фабрика пула по настройкам: DataImpulse-шлюз или статический пул.

    Возвращает None, если прокси не настроены (HttpClient ходит напрямую).
    """
    if settings.proxy_dataimpulse_user and settings.proxy_dataimpulse_pass:
        url = build_dataimpulse_proxy_url(
            settings.proxy_dataimpulse_user,
            settings.proxy_dataimpulse_pass,
            host=settings.proxy_dataimpulse_host,
            port=settings.proxy_dataimpulse_port,
            geo=settings.proxy_dataimpulse_geo,
        )
        pool: ProxyPool | GatewayProxyPool = GatewayProxyPool(
            url,
            sticky_ttl_sec=settings.proxy_dataimpulse_sticky_ttl_sec,
            fail_threshold=settings.proxy_dataimpulse_fail_threshold,
            fail_cooldown_sec=settings.proxy_dataimpulse_fail_cooldown_sec,
            url_kwargs={
                "user": settings.proxy_dataimpulse_user,
                "password": settings.proxy_dataimpulse_pass,
                "host": settings.proxy_dataimpulse_host,
                "port": settings.proxy_dataimpulse_port,
                "geo": settings.proxy_dataimpulse_geo,
            },
        )
        return pool
    if redis is not None:
        return ProxyPool(redis)
    return None
