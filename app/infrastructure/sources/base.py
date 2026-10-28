"""SourceAdapter — единый протокол доступа к источникам (ТЗ п. 5.6, 6.2).

Принципы (ТЗ п. 6.1):
- Один адаптер = один источник.
- Код адаптера в Git, конфиг — в БД (`sources`).
- `raw_payload` (сырой ответ адаптера) сохраняется в S3/MinIO для перепарсинга
  (`listings.raw_key`, PLAN.md фаза 6).

`BaseApiAdapter` не применяет `rate_limit` самостоятельно: поле сохраняется
для `AdapterRegistry`, который оборачивает `fetch` в лимитер. Если registry
не делает этого — лимит не enforced (отдельная задача TECHDEBT).
"""

import logging
from types import TracebackType
from typing import Any, Protocol, runtime_checkable

from app.infrastructure.sources.common.http import FetchError, HttpClient
from app.domain.search.criteria import SearchCriteria
from app.domain.listings.unified import FetchResult

__all__ = ["FetchError", "HttpClient", "SourceAdapter", "BaseApiAdapter"]

logger = logging.getLogger(__name__)


@runtime_checkable
class SourceAdapter(Protocol):
    """Протокол адаптера источника.

    Атрибуты:
        source_name: Каноническое имя источника (`encar`, `kcar`, `che168`).
    """

    source_name: str

    async def fetch(
        self,
        criteria: SearchCriteria,
        cursor: str | None = None,
    ) -> FetchResult:
        """Забирает страницу объявлений по критериям.

        Args:
            criteria: Критерии поиска.
            cursor: Курсор пагинации (источник-специфичный) или `None`.

        Returns:
            Страница объявлений; пустой `FetchResult` — валидный результат
            «ничего не найдено».
        """
        ...

    async def healthcheck(self) -> bool:
        """Проверяет доступность источника.

        Returns:
            `True`, если источник отвечает и отдаёт валидный ответ;
            `False` — при любой ошибке (сеть, 5xx, парсинг).
        """
        ...

    async def close(self) -> None:
        """Освобождает ресурсы адаптера (HTTP-клиент)."""
        ...


def _normalize_credentials(
    credentials: dict[str, Any] | str | None,
) -> dict[str, Any]:
    """Приводит credentials к dict; отклоняет строку.

    Раньше `credentials or {}` сохранял `str` как есть, и адаптер падал
    на `self.credentials["key"]` в рантайме. Здесь — явная ошибка на
    конфигурации, чтобы падать рано.

    Args:
        credentials: `dict`, JSON-строка или `None`.

    Returns:
        Словарь credentials (пустой для `None`/пустой строки).

    Raises:
        TypeError: Если передана непустая строка — источник должен
            отдавать структурированные credentials, а не строку.
    """
    if credentials is None or credentials == "":
        return {}
    if isinstance(credentials, str):
        raise TypeError(
            "credentials must be a dict, not str; "
            "parse JSON in AdapterRegistry before passing to the adapter"
        )
    return dict(credentials)


def _mask_credentials(credentials: dict[str, Any]) -> dict[str, Any]:
    """Маскирует значения credentials для безопасного repr.

    Args:
        credentials: Словарь credentials.

    Returns:
        Словарь с `"***"` вместо значений.
    """
    return {key: "***" for key in credentials}


class BaseApiAdapter:
    """Общая база для API-адаптеров.

    Инстанс собирается AdapterRegistry из строки таблицы `sources`
    (name, config, credentials, rate_limit, timeout, capabilities).

    Атрибуты:
        source_name: Каноническое имя источника.
        config: Источник-специфичный конфиг (URL, селекторы, флаги).
        credentials: API-ключи; маскируются в `repr()`.
        rate_limit: Запросов в минуту; **применяется AdapterRegistry**,
            не самим адаптером.
        timeout: Таймаут HTTP-запроса (сек); игнорируется, если передан
            готовый `http_client`.
        capabilities: Флаги источника. Известные ключи:
            `use_proxy: bool`, `supports_cursor: bool`.
        http: HTTP-клиент (общий или собранный из `timeout`).
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
        translator: Any = None,
    ) -> None:
        if rate_limit <= 0:
            raise ValueError(f"rate_limit must be > 0, got {rate_limit}")
        if timeout <= 0:
            raise ValueError(f"timeout must be > 0, got {timeout}")
        self.source_name = source_name
        self.config: dict[str, Any] = config or {}
        self.credentials: dict[str, Any] = _normalize_credentials(credentials)
        self.rate_limit = rate_limit
        self.timeout = timeout
        self.capabilities: dict[str, Any] = capabilities or {}
        # HttpClient можно подменить в тестах (transport-инъекция).
        # Внимание: при передаче http_client параметр `timeout` игнорируется —
        # за таймаут отвечает переданный клиент.
        self.http = http_client or HttpClient(timeout=timeout)
        # Переводчик (микросервис NLLB): объект с async translate(texts,
        # source_lang, target_lang) -> list[str | None]. Используется
        # источниками с корейским контентом (encar) для полей объявления
        # и отчёта осмотра. None — перевод выключен (остаётся оригинал).
        self.translator = translator
        logger.debug(
            "adapter_initialized source=%s rate_limit=%d timeout=%d capabilities=%s",
            source_name,
            rate_limit,
            timeout,
            sorted(self.capabilities),
        )

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}("
            f"source_name={self.source_name!r}, "
            f"credentials={_mask_credentials(self.credentials)!r}, "
            f"rate_limit={self.rate_limit}, "
            f"timeout={self.timeout})"
        )

    async def fetch(
        self, criteria: SearchCriteria, cursor: str | None = None
    ) -> FetchResult:
        """Забирает страницу объявлений. Реализуется подклассом."""
        raise NotImplementedError(f"{type(self).__name__} must implement fetch()")

    async def healthcheck(self) -> bool:
        """Проверяет доступность источника. Реализуется подклассом."""
        raise NotImplementedError(
            f"{type(self).__name__} must implement healthcheck()"
        )

    async def close(self) -> None:
        """Закрывает HTTP-клиент. Идемпотентно; ошибки логируются."""
        try:
            await self.http.close()
        except Exception:  # noqa: BLE001 — cleanup не должен маскировать ошибку
            logger.exception("adapter_close_failed source=%s", self.source_name)

    async def __aenter__(self) -> "BaseApiAdapter":
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()