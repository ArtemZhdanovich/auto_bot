"""SourceAdapter — единый протокол доступа к источникам (ТЗ п. 5.6, 6.2).

Принципы (ТЗ п. 6.1):
- Один адаптер = один источник.
- Код адаптера в Git, конфиг — в БД (`sources`).
- `raw_payload` сохраняется для перепарсинга.
"""

from typing import Any, Protocol, runtime_checkable

from app.adapters.common.http import FetchError, HttpClient
from app.schemas.criteria import SearchCriteria
from app.schemas.listing import FetchResult

__all__ = ["FetchError", "HttpClient", "SourceAdapter", "BaseApiAdapter"]


@runtime_checkable
class SourceAdapter(Protocol):
    source_name: str

    async def fetch(
        self,
        criteria: SearchCriteria,
        cursor: str | None = None,
    ) -> FetchResult: ...

    async def healthcheck(self) -> bool: ...


class BaseApiAdapter:
    """Общая база для API-адаптеров.

    Инстанс собирается AdapterRegistry из строки таблицы `sources`
    (name, config, credentials, rate_limit, timeout, capabilities).
    """

    source_name: str = "unknown"

    def __init__(
        self,
        *,
        source_name: str,
        config: dict[str, Any] | None = None,
        credentials: dict[str, Any] | str | None = None,
        rate_limit: int = 60,
        timeout: int = 30,
        capabilities: dict[str, Any] | None = None,
        http_client: HttpClient | None = None,
    ) -> None:
        self.source_name = source_name
        self.config: dict[str, Any] = config or {}
        self.credentials = credentials or {}
        self.rate_limit = rate_limit
        self.timeout = timeout
        self.capabilities: dict[str, Any] = capabilities or {}
        # HttpClient можно подменить в тестах (transport-инъекция)
        self.http = http_client or HttpClient(timeout=timeout)

    async def fetch(
        self, criteria: SearchCriteria, cursor: str | None = None
    ) -> FetchResult:
        raise NotImplementedError

    async def healthcheck(self) -> bool:
        raise NotImplementedError

    async def close(self) -> None:
        await self.http.close()
