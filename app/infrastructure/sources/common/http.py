"""HTTP-клиент с прокси и ретраями (ТЗ п. 6.3)."""

import asyncio
import logging
import random
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from app.infrastructure.sources.common.proxy import ProxyPool

logger = logging.getLogger(__name__)

# Транзиентные коды, которые имеет смысл ретраить.
_RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})
# Cap на экспоненциальный backoff (сек), чтобы 2**attempt не улетал в минуты.
_BACKOFF_CAP_SEC = 30.0
# Базовая задержка перед первым ретраем (сек).
_BACKOFF_BASE_SEC = 1.0


class FetchError(RuntimeError):
    """Не удалось получить данные от источника."""


def _parse_retry_after(value: str | None) -> float | None:
    """Парсит заголовок `Retry-After` (секунды или HTTP-date).

    Args:
        value: Значение заголовка `Retry-After` или `None`.

    Returns:
        Задержка в секундах (>= 0) или `None`, если заголовка нет/невалиден.
    """
    if not value:
        return None
    value = value.strip()
    try:
        seconds = float(value)
        return seconds if seconds >= 0 else None
    except ValueError:
        pass
    try:
        dt = parsedate_to_datetime(value)
        # tz-aware datetime → unix ts; устаревший/битый формат — None
        now = parsedate_to_datetime(  # текущее время в том же формате
            "Thu, 01 Jan 1970 00:00:00 GMT"
        )
        _ = now  # только для ясности
        from datetime import datetime, timezone

        delta = (dt - datetime.now(timezone.utc)).total_seconds()
        return max(delta, 0.0)
    except (TypeError, ValueError, OverflowError):
        return None


def _backoff(attempt: int, base: float = _BACKOFF_BASE_SEC) -> float:
    """Экспоненциальный backoff с cap и jitter.

    Args:
        attempt: Номер попытки (0-based).
        base: Базовая задержка в секундах.

    Returns:
        Задержка в секундах: `base * 2**attempt`, ограниченная `cap`,
        плюс случайный jitter до 50% (анти thundering-herd).
    """
    raw = min(base * (2**attempt), _BACKOFF_CAP_SEC)
    return raw + random.uniform(0, raw * 0.5)


class HttpClient:
    """GET с ротацией прокси, ретраями и обработкой 429.

    Клиент намеренно не держит постоянный `httpx.AsyncClient`: прокси
    меняется между попытками. Создание клиента вынесено **за цикл ретраев**,
    чтобы на одну попытку был ровно один TLS-handshake.
    """

    def __init__(
        self,
        proxy_pool: ProxyPool | None = None,
        timeout: int | float | httpx.Timeout = 30,
        max_retries: int = 3,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if max_retries < 1:
            raise ValueError("max_retries must be >= 1")
        self._proxy_pool = proxy_pool
        # connect не должен съедать весь бюджет: min(5, timeout)
        if isinstance(timeout, httpx.Timeout):
            self._timeout = timeout
        else:
            self._timeout = httpx.Timeout(
                timeout, connect=min(5.0, float(timeout))
            )
        self._max_retries = max_retries
        # transport — точка инъекции MockTransport в тестах
        self._transport = transport

    async def get(self, url: str, **kwargs: Any) -> httpx.Response:
        """GET с ретраями (см. `_request`)."""
        return await self._request("GET", url, **kwargs)

    async def post(self, url: str, **kwargs: Any) -> httpx.Response:
        """POST с ретраями (см. `_request`)."""
        return await self._request("POST", url, **kwargs)

    async def get_json(self, url: str, **kwargs: Any) -> Any:
        """GET + JSON-декодирование; ошибки парсинга → `FetchError`.

        Args:
            url: URL запроса.
            **kwargs: Прокидываются в `get`.

        Returns:
            Раскодированный JSON.

        Raises:
            FetchError: Сетевая ошибка, не-2xx или невалидный JSON.
        """
        resp = await self.get(url, **kwargs)
        try:
            return resp.json()
        except ValueError as exc:  # JSONDecodeError — подкласс ValueError
            raise FetchError(f"Invalid JSON from {url}") from exc

    async def post_json(self, url: str, **kwargs: Any) -> Any:
        """POST + JSON-декодирование; ошибки парсинга → `FetchError`.

        Args:
            url: URL запроса.
            **kwargs: Прокидываются в `post`.

        Returns:
            Раскодированный JSON.

        Raises:
            FetchError: Сетевая ошибка, не-2xx или невалидный JSON.
        """
        resp = await self.post(url, **kwargs)
        try:
            return resp.json()
        except ValueError as exc:
            raise FetchError(f"Invalid JSON from {url}") from exc

    async def close(self) -> None:
        """Совместимость с адаптерами: клиенты создаются на запрос."""
        return None

    # --- internals ------------------------------------------------------

    async def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        """Выполняет запрос с ретраями, ротацией прокси и уважением 429.

        Ретраятся: сетевые ошибки (`httpx.NetworkError` и наследники),
        `TimeoutException`, а также HTTP-коды `_RETRYABLE_STATUS`.
        Не ретраятся прочие 4xx (клиентская ошибка — повтор бессмыслен).

        Args:
            method: HTTP-метод.
            url: URL запроса.
            **kwargs: Прокидываются в `httpx.AsyncClient.request`.

        Returns:
            Успешный `httpx.Response` (2xx).

        Raises:
            FetchError: Все попытки исчерпаны или получен неретраибельный
                статус.
        """
        last_error: Exception | None = None
        for attempt in range(self._max_retries):
            proxy = await self._proxy_pool.get() if self._proxy_pool else None
            try:
                async with httpx.AsyncClient(
                    proxy=proxy,
                    timeout=self._timeout,
                    follow_redirects=True,
                    transport=self._transport,
                ) as client:
                    resp = await client.request(method, url, **kwargs)

                    if resp.status_code in _RETRYABLE_STATUS:
                        delay = self._retry_delay(resp, attempt)
                        logger.warning(
                            "Retrying %s %s: HTTP %d, attempt %d/%d, sleep %.1fs",
                            method,
                            url,
                            resp.status_code,
                            attempt + 1,
                            self._max_retries,
                            delay,
                        )
                        await asyncio.sleep(delay)
                        continue

                    resp.raise_for_status()
                    return resp

            except httpx.TimeoutException as exc:
                last_error = exc
                if self._proxy_pool and proxy:
                    await self._proxy_pool.mark_bad(proxy)
                delay = _backoff(attempt)
                logger.warning(
                    "Timeout %s %s: %r, attempt %d/%d, sleep %.1fs",
                    method,
                    url,
                    exc,
                    attempt + 1,
                    self._max_retries,
                    delay,
                )
                await asyncio.sleep(delay)
                continue

            except httpx.TransportError as exc:
                # Покрывает ConnectError, ReadError, WriteError, ProxyError,
                # а также RemoteProtocolError (это ProtocolError, НЕ
                # NetworkError — раньше выпадал из ретраев).
                last_error = exc
                if self._proxy_pool and proxy:
                    await self._proxy_pool.mark_bad(proxy)
                logger.warning(
                    "Network error %s %s: %r, attempt %d/%d",
                    method,
                    url,
                    exc,
                    attempt + 1,
                    self._max_retries,
                )
                continue

            except httpx.HTTPStatusError as exc:
                # Сюда попадают только НЕретраибельные 4xx
                raise FetchError(
                    f"HTTP {exc.response.status_code} for {url}"
                ) from exc

        raise FetchError(f"Failed to fetch {url}: {last_error}") from last_error

    @staticmethod
    def _retry_delay(resp: httpx.Response, attempt: int) -> float:
        """Задержка перед ретраем: `Retry-After` (если есть) или backoff.

        Args:
            resp: Ответ с ретраибельным статусом.
            attempt: Номер попытки (0-based).

        Returns:
            Задержка в секундах (>= 0).
        """
        retry_after = _parse_retry_after(resp.headers.get("Retry-After"))
        if retry_after is not None:
            # cap защищает от злонамеренно огромного Retry-After
            return min(retry_after, _BACKOFF_CAP_SEC)
        return _backoff(attempt)