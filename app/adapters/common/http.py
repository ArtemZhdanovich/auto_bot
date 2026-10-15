"""HTTP-клиент с прокси и ретраями (ТЗ п. 6.3)."""

import asyncio
import logging
from typing import Any

import httpx

from app.adapters.common.proxy import ProxyPool

logger = logging.getLogger(__name__)


class FetchError(RuntimeError):
    """Не удалось получить данные от источника."""


class HttpClient:
    """GET с ротацией прокси, ретраями и обработкой 429."""

    def __init__(
        self,
        proxy_pool: ProxyPool | None = None,
        timeout: int = 30,
        max_retries: int = 3,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._proxy_pool = proxy_pool
        self._timeout = timeout
        self._max_retries = max_retries
        # transport — точка инъекции MockTransport в тестах
        self._transport = transport

    async def get(self, url: str, **kwargs: Any) -> httpx.Response:
        return await self._request("GET", url, **kwargs)

    async def post(self, url: str, **kwargs: Any) -> httpx.Response:
        return await self._request("POST", url, **kwargs)

    async def get_json(self, url: str, **kwargs: Any) -> Any:
        resp = await self.get(url, **kwargs)
        return resp.json()

    async def post_json(self, url: str, **kwargs: Any) -> Any:
        resp = await self.post(url, **kwargs)
        return resp.json()

    async def close(self) -> None:
        """Совместимость с адаптерами: клиенты создаются на запрос."""
        return None

    # --- internals ------------------------------------------------------

    async def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
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
                    if resp.status_code == 429:
                        await asyncio.sleep(2**attempt)
                        continue
                    resp.raise_for_status()
                    return resp
            except (httpx.ProxyError, httpx.ConnectError, httpx.TimeoutException) as exc:
                last_error = exc
                if self._proxy_pool and proxy:
                    await self._proxy_pool.mark_bad(proxy)
                if isinstance(exc, httpx.TimeoutException):
                    await asyncio.sleep(2**attempt)
                continue
            except httpx.HTTPStatusError as exc:
                # 4xx/5xx после raise_for_status — не ретраим
                raise FetchError(f"HTTP {exc.response.status_code} for {url}") from exc
        raise FetchError(f"Failed to fetch {url}: {last_error}")
