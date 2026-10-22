"""Пул резидентских прокси в Redis (ТЗ п. 6.3).

Ключи: `proxy:good`, `proxy:bad`, `proxy:cooldown`.

`ProxyPool` хранит статические IP в Redis; `GatewayProxyPool` — один
ротационный шлюз (DataImpulse). `mark_bad` у `ProxyPool` атомарен
(MULTI/EXEC); `GatewayProxyPool` держит cooldown in-memory (per-worker —
это осознанный tradeoff, см. докстринг класса).
"""

import logging
import time
from typing import Any, Callable
from urllib.parse import quote, urlsplit, urlunsplit

from pydantic import SecretStr
from redis.asyncio import Redis

logger = logging.getLogger(__name__)

GOOD_KEY = "proxy:good"
BAD_KEY = "proxy:bad"
COOLDOWN_KEY = "proxy:cooldown"


def _secret_to_str(value: str | SecretStr | None) -> str | None:
    """Извлекает строку из `SecretStr` (или возвращает `str`/`None` как есть).

    Args:
        value: `str`, `SecretStr` или `None`.

    Returns:
        Строка без обёртки или `None`.
    """
    if value is None:
        return None
    if isinstance(value, SecretStr):
        return value.get_secret_value()
    return value


def _has_secret(value: str | SecretStr | None) -> bool:
    """`True`, если значение задано и непустое (с учётом `SecretStr`).

    `bool(SecretStr(""))` — `True`; поэтому проверять только через
    `get_secret_value()`.

    Args:
        value: `str`, `SecretStr` или `None`.

    Returns:
        `True`, если строка непустая.
    """
    return bool(_secret_to_str(value))


def _mask_proxy_url(url: str) -> str:
    """Маскирует `user:password@` в URL для безопасного логирования.

    Args:
        url: URL прокси или любой URL с возможными credentials.

    Returns:
        URL с `***:***@` вместо credentials.
    """
    try:
        parts = urlsplit(url)
    except ValueError:
        return "<invalid-url>"
    if parts.username is None and parts.password is None:
        return url
    netloc = f"***:***@{parts.hostname or ''}"
    if parts.port:
        netloc += f":{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


class ProxyPool:
    """Пул статических прокси в Redis.

    Состояния хранятся в Redis, поэтому пул разделяется между воркерами.
    Восстановление (`restore`) инициируется вызывающим кодом — например,
    healthcheck-шедулером или `HttpClient` при успешном запросе.
    Автоматического cooldown в этой реализации нет: `COOLDOWN_KEY`
    зарезервирован под будущий Scheduler (этап 8).
    """

    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def get(self) -> str | None:
        """Случайный рабочий прокси или None (прямой запрос), если пул пуст.

        Returns:
            URL прокси или `None`.
        """
        proxy = await self._redis.srandmember(GOOD_KEY)
        if proxy is None:
            return None
        return proxy.decode() if isinstance(proxy, bytes) else proxy

    async def add(self, *proxies: str) -> None:
        """Добавляет прокси в пул good.

        Args:
            *proxies: URL прокси.
        """
        if proxies:
            await self._redis.sadd(GOOD_KEY, *proxies)

    async def mark_bad(self, proxy: str) -> None:
        """Атомарно переносит прокси из good в bad.

        MULTI/EXEC гарантирует, что при краше процесса прокси не исчезнет
        из обоих множеств (раньше `srem` + `sadd` могли оставить его нигде).

        Args:
            proxy: URL прокси.
        """
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.srem(GOOD_KEY, proxy)
            pipe.sadd(BAD_KEY, proxy)
            await pipe.execute()
        logger.warning("proxy_marked_bad: %s", _mask_proxy_url(proxy))

    async def restore(self, proxy: str) -> None:
        """Вернуть прокси из bad/cooldown в good после успешного healthcheck.

        Args:
            proxy: URL прокси.
        """
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.srem(BAD_KEY, proxy)
            pipe.srem(COOLDOWN_KEY, proxy)
            pipe.sadd(GOOD_KEY, proxy)
            await pipe.execute()

    async def healthcheck_all(self) -> dict[str, int]:
        """Поддерживающая проверка: считает прокси по группам.

        Полноценный healthcheck выполняется Scheduler'ом (этап 8).

        Returns:
            Словарь `{"good": N, "bad": N, "cooldown": N}`.
        """
        return {
            "good": await self._redis.scard(GOOD_KEY),
            "bad": await self._redis.scard(BAD_KEY),
            "cooldown": await self._redis.scard(COOLDOWN_KEY),
        }


def build_dataimpulse_proxy_url(
    user: str,
    password: str | SecretStr,
    host: str = "gw.dataimpulse.com",
    port: int = 823,
    geo: str | None = None,
    sticky_window: str | None = None,
) -> str:
    """URL шлюза DataImpulse по официальному формату доков.

    Параметры дописываются к логину после "__", разделяются ";",
    key.value: cr.cn (страна), sessid.X (sticky ~30 мин). Пример:
    http://login__cr.cn;sessid.42:pass@gw.dataimpulse.com:823

    Args:
        user: Логин провайдера.
        password: Пароль (str или SecretStr — распаковывается).
        host: Хост шлюза.
        port: Порт шлюза.
        geo: Гео-параметр (`cr.X`) или `None`.
        sticky_window: Идентификатор sticky-окна или `None`.

    Returns:
        URL прокси.

    Raises:
        ValueError: Если `password` пуст после распаковки или `host` пуст.
    """
    password_str = _secret_to_str(password)
    if not password_str:
        raise ValueError("proxy password is empty")
    if not host:
        raise ValueError("proxy host is empty")

    username = quote(user, safe="")
    params: list[str] = []
    if geo:
        params.append(f"cr.{geo}")
    if sticky_window:
        params.append(f"sessid.{sticky_window}")
    if params:
        username += "__" + ";".join(params)
    return f"http://{username}:{quote(password_str, safe='')}@{host}:{port}"


class GatewayProxyPool:
    """Пул из одного ротационного шлюза (DataImpulse и совместимые).

    Отличия от ProxyPool (статические IP):
    - ротацию IP делает провайдер (per request или sessid-окно) —
      нам не нужно хранить список IP и bad-статусы;
    - mark_bad() не выбрасывает шлюз навсегда: только N ошибок подряд
      (ConnectError и т.п.) усыпляют его на cooldown — после этого
      get() возвращает None и HttpClient ходит напрямую, пока пауза
      не истечёт. Так обрыв шлюза не убивает парсинг целиком.

    Состояние (`_fails`, `_cooldown_until`) хранится in-memory и **не
    разделяется между воркерами**: каждый процесс ведёт свой cooldown.
    Для multi-worker развёртывания либо используйте один worker на шлюз,
    либо вынесите счётчик в Redis (отдельная задача TECHDEBT).
    """

    def __init__(
        self,
        url: str,
        *,
        sticky_ttl_sec: int = 0,
        fail_threshold: int = 5,
        fail_cooldown_sec: int = 300,
        url_kwargs: dict[str, Any] | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._url = url
        self._url_kwargs = url_kwargs or {}
        self._sticky_ttl = sticky_ttl_sec
        self._fail_threshold = fail_threshold
        self._fail_cooldown_sec = fail_cooldown_sec
        self._clock = clock or time.monotonic
        self._fails = 0
        self._cooldown_until = 0.0

    def __repr__(self) -> str:
        return (
            f"GatewayProxyPool(url={_mask_proxy_url(self._url)!r}, "
            f"fails={self._fails}, "
            f"cooldown_until={self._cooldown_until:.1f})"
        )

    def _sticky_window(self) -> str | None:
        """Стабильный sessid в пределах окна — детерминированно, без состояния.

        Returns:
            Идентификатор окна или `None`, если sticky выключен
            (`sticky_ttl_sec <= 0` — ротация на каждый запрос).
        """
        if self._sticky_ttl <= 0:
            return None
        return str(int(self._clock() // self._sticky_ttl))

    async def get(self) -> str | None:
        """URL шлюза или `None` во время cooldown.

        Returns:
            URL прокси либо `None` (HttpClient ходит напрямую).
        """
        if self._clock() < self._cooldown_until:
            return None
        if self._sticky_ttl <= 0:
            return self._url
        return build_dataimpulse_proxy_url(
            **self._url_kwargs, sticky_window=self._sticky_window()
        )

    async def mark_bad(self, proxy: str) -> None:
        """Регистрирует сбой шлюза; по достижении порога — cooldown.

        Args:
            proxy: URL прокси, полученный из `get()` (не логируется).
        """
        self._fails += 1
        if self._fails >= self._fail_threshold:
            self._cooldown_until = self._clock() + self._fail_cooldown_sec
            self._fails = 0
            logger.warning(
                "gateway_proxy_cooldown: %s sec (fails)", self._fail_cooldown_sec
            )

    async def restore(self, proxy: str) -> None:
        """Сбрасывает счётчик сбоев после успешного запроса.

        Args:
            proxy: URL прокси (не используется; сигнатура для совместимости).
        """
        self._fails = 0

    async def healthcheck_all(self) -> dict[str, int]:
        """Возвращает статус шлюза (всегда один шлюз).

        Returns:
            `{"gateway": 1, "cooldown": 0|1}`.
        """
        on_cooldown = 1 if self._clock() < self._cooldown_until else 0
        return {"gateway": 1, "cooldown": on_cooldown}


def build_proxy_pool(
    settings: Any, redis: Redis | None
) -> ProxyPool | GatewayProxyPool | None:
    """Фабрика пула по настройкам: DataImpulse-шлюз или статический пул.

    Возвращает None, если прокси не настроены (HttpClient ходит напрямую).

    Args:
        settings: Настройки приложения (`Settings`).
        redis: Клиент Redis; нужен только для статического `ProxyPool`.

    Returns:
        Пул прокси или `None`.
    """
    if settings.proxy_dataimpulse_user and _has_secret(
        settings.proxy_dataimpulse_pass
    ):
        password = _secret_to_str(settings.proxy_dataimpulse_pass)
        assert password is not None  # проверено _has_secret
        url = build_dataimpulse_proxy_url(
            settings.proxy_dataimpulse_user,
            password,
            host=settings.proxy_dataimpulse_host,
            port=settings.proxy_dataimpulse_port,
            geo=settings.proxy_dataimpulse_geo,
        )
        return GatewayProxyPool(
            url,
            sticky_ttl_sec=settings.proxy_dataimpulse_sticky_ttl_sec,
            fail_threshold=settings.proxy_dataimpulse_fail_threshold,
            fail_cooldown_sec=settings.proxy_dataimpulse_fail_cooldown_sec,
            url_kwargs={
                "user": settings.proxy_dataimpulse_user,
                "password": password,
                "host": settings.proxy_dataimpulse_host,
                "port": settings.proxy_dataimpulse_port,
                "geo": settings.proxy_dataimpulse_geo,
            },
        )
    if redis is not None:
        return ProxyPool(redis)
    return None