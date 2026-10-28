"""AdapterRegistry — динамическая сборка адаптеров из БД (ТЗ п. 6.5).

Код в Git, конфиг в БД: класс берётся из ADAPTER_CLASSES,
параметры — из строки таблицы `sources`. Секреты не хранятся в БД:
`credentials_ref` — имя переменной окружения, содержащей JSON-объект
с ключами.

Пример:
    # В окружении:
    # ENCAR_CREDENTIALS='{"api_key": "xxx", "api_secret": "yyy"}'
    # В таблице sources:
    #   name=encar, credentials_ref='ENCAR_CREDENTIALS', ...
    # Адаптер получает credentials={"api_key": "xxx", "api_secret": "yyy"}
"""

import asyncio
import json
import logging
import os
from typing import Any, Protocol

from sqlalchemy import select

from app.infrastructure.sources import ADAPTER_CLASSES
from app.infrastructure.sources.base import BaseApiAdapter
from app.infrastructure.sources.common.http import HttpClient
from app.infrastructure.db.models import Source

logger = logging.getLogger(__name__)

# Префиксы, разрешённые для credentials_ref. Защита от опечаток и от
# чтения системных переменных (PATH, LD_PRELOAD, AWS_SECRET_ACCESS_KEY и т.п.).
_ALLOWED_REF_PREFIXES: tuple[str, ...] = (
    "ADAPTER_",
    "SOURCE_",
    "SECRET_",
    "CREDENTIALS_",
)


class SecretError(RuntimeError):
    """Секрет не найден, невалиден или запрещён к чтению."""


class SecretStore(Protocol):
    """Протокол хранилища секретов.

    Реализации: `EnvSecretStore` (dev/single-host), Vault/AWS SM (этап 8).
    """

    async def get(self, ref: str) -> dict[str, Any] | None:
        """Возвращает секрет по ссылке.

        Args:
            ref: Имя ссылки (обычно env-переменная).

        Returns:
            Словарь с секретами или `None`, если ref не найден.

        Raises:
            SecretError: Если ref запрещён или содержимое невалидно.
        """
        ...


class EnvSecretStore:
    """Простой store секретов: credentials_ref = имя переменной окружения.

    Значение переменной — JSON-объект: `'{"api_key": "...", "token": "..."}'`.
    Простой строковый токен поддерживается как `{"token": <value>}` для
    обратной совместимости.

    Vault/AWS SM подключаются на этапе 8 (ТЗ п. 11.6, SECRET_STORE).
    """

    async def get(self, ref: str) -> dict[str, Any] | None:
        """Читает секрет из env по ref.

        Args:
            ref: Имя переменной окружения. Должно начинаться с одного из
                `_ALLOWED_REF_PREFIXES`.

        Returns:
            Словарь секретов или `None`, если переменная не задана.

        Raises:
            SecretError: Если префикс ref не разрешён, или значение не
                является JSON-объектом/невалидным JSON.
        """
        if not any(ref.startswith(prefix) for prefix in _ALLOWED_REF_PREFIXES):
            raise SecretError(
                f"credentials_ref={ref!r} must start with one of "
                f"{_ALLOWED_REF_PREFIXES}"
            )
        raw = os.environ.get(ref)
        if raw is None:
            return None
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            # Backward compat: одиночный токен без JSON.
            logger.info("credentials_ref=%s is not JSON, wrapped as {'token': ...}", ref)
            return {"token": raw}
        if not isinstance(data, dict):
            raise SecretError(
                f"credentials_ref={ref!r} must be a JSON object, "
                f"got {type(data).__name__}"
            )
        return data


def _positive_int(value: Any, default: int, *, name: str, source: str) -> int:
    """Валидирует int-поле из БД: > 0, иначе `default`.

    Args:
        value: Значение из БД (может быть `None`, `<= 0`, нечисло).
        default: Значение по умолчанию.
        name: Имя поля (для лога).
        source: Имя источника (для лога).

    Returns:
        Положительное целое.
    """
    try:
        result = int(value) if value is not None else default
    except (TypeError, ValueError):
        logger.warning(
            "invalid_%s source=%s value=%r, using default=%d",
            name, source, value, default,
        )
        return default
    if result <= 0:
        logger.warning(
            "non_positive_%s source=%s value=%d, using default=%d",
            name, source, result, default,
        )
        return default
    return result


class AdapterRegistry:
    """Реестр адаптеров: держит кеш `name -> BaseApiAdapter`.

    Кеш заменяется атомарно (`load`) или точечно (`reload_source`);
    старые адаптеры закрываются после успешной замены. `get()` — sync
    и без lock: атомарная замена кеша гарантирует консистентность без
    блокировки читателей.
    """

    def __init__(
        self,
        session_factory: Any,
        secret_store: SecretStore | None = None,
        proxy_pool: Any | None = None,
        translator: Any = None,
    ) -> None:
        self._session_factory = session_factory
        self._secrets: SecretStore = secret_store or EnvSecretStore()
        self._proxy_pool = proxy_pool
        # Переводчик (NLLB-микросервис) — пробрасывается в адаптеры
        # источников с корейским контентом (encar).
        self._translator = translator
        self._cache: dict[str, BaseApiAdapter] = {}
        self._reload_lock = asyncio.Lock()

    async def load(self) -> None:
        """Собирает все включённые адаптеры и атомарно заменяет кеш.

        Ошибка сборки одного источника не прерывает загрузку остальных:
        источник пропускается с логом. Старый кеш остаётся доступным
        до полной сборки нового.
        """
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    select(Source).where(Source.enabled).order_by(Source.priority)
                )
            ).scalars().all()

        new_cache: dict[str, BaseApiAdapter] = {}
        failures = 0
        for row in rows:
            try:
                new_cache[row.name] = await self._build(row)
            except Exception:  # noqa: BLE001 — один источник не должен ронять все
                failures += 1
                logger.exception(
                    "adapter_build_failed source=%s cls=%s",
                    row.name,
                    row.adapter_class,
                )

        # Атомарная замена: читатели видят либо старый кеш целиком, либо новый.
        old = self._cache
        self._cache = new_cache

        # Закрываем и удалённые, и заменённые адаптеры: при reload с тем же
        # именем старый инстанс иначе остаётся с открытым httpx-клиентом.
        for name, adapter in old.items():
            replacement = new_cache.get(name)
            if replacement is None or replacement is not adapter:
                await self._safe_close(adapter, name)

        logger.info(
            "adapters_loaded total=%d built=%d failed=%d",
            len(rows),
            len(new_cache),
            failures,
        )

    async def _build(self, row: Source) -> BaseApiAdapter:
        """Собирает адаптер по строке `sources`.

        Args:
            row: Строка таблицы `sources`.

        Returns:
            Инициализированный адаптер.

        Raises:
            SecretError: Если `credentials_ref` задан, но секрет не найден
                или невалиден.
            KeyError: Если `adapter_class` не зарегистрирован в ADAPTER_CLASSES.
        """
        cls = ADAPTER_CLASSES.get(row.adapter_class)
        if cls is None:
            raise KeyError(
                f"adapter_class_not_found: name={row.name} cls={row.adapter_class}"
            )

        # credentials: ref задан → секрет обязателен, иначе fail-fast.
        credentials: dict[str, Any] | None = None
        if row.credentials_ref:
            credentials = await self._secrets.get(row.credentials_ref)
            if credentials is None:
                raise SecretError(
                    f"credentials_ref={row.credentials_ref!r} not found "
                    f"for source={row.name}"
                )

        # config/capabilities должны быть dict; строки/None — warning + {}.
        config = row.config if isinstance(row.config, dict) else {}
        if row.config is not None and not isinstance(row.config, dict):
            logger.warning(
                "source_config_not_dict source=%s type=%s",
                row.name, type(row.config).__name__,
            )
        capabilities = row.capabilities if isinstance(row.capabilities, dict) else {}
        if row.capabilities is not None and not isinstance(row.capabilities, dict):
            logger.warning(
                "source_capabilities_not_dict source=%s type=%s",
                row.name, type(row.capabilities).__name__,
            )

        timeout = _positive_int(
            row.timeout_sec, 30, name="timeout_sec", source=row.name
        )
        rate_limit = _positive_int(
            row.rate_limit_per_min, 60, name="rate_limit_per_min", source=row.name
        )

        # Платный шлюз-прокси — только для источников с use_proxy: true.
        # `is True` — защита от truthy-строк "false" из БД.
        use_proxy = capabilities.get("use_proxy") is True
        http_client = None
        if self._proxy_pool is not None and use_proxy:
            http_client = HttpClient(proxy_pool=self._proxy_pool, timeout=timeout)

        return cls(
            source_name=row.name,
            config=config,
            credentials=credentials,
            rate_limit=rate_limit,
            timeout=timeout,
            capabilities=capabilities,
            http_client=http_client,
            translator=self._translator,
        )

    async def reload_source(self, name: str) -> BaseApiAdapter | None:
        """Пересобрать один адаптер из БД (после смены sources.config).

        Используется ParserDoctor: repaired-конфиг подхватывается без
        рестарта воркера. Источник, не найденный в БД или выключенный,
        удаляется из кэша.

        Args:
            name: Имя источника (`sources.name`).

        Returns:
            Новый адаптер или `None`, если источник выключен/удалён.

        Raises:
            SecretError: Если `credentials_ref` не разрешается.
            KeyError: Если `adapter_class` не зарегистрирован.
        """
        async with self._reload_lock:
            async with self._session_factory() as session:
                row = (
                    await session.execute(select(Source).where(Source.name == name))
                ).scalar_one_or_none()

            if row is None or not row.enabled:
                old = self._cache.pop(name, None)
                if old is not None:
                    await self._safe_close(old, name)
                return None

            adapter = await self._build(row)
            old = self._cache.get(name)
            self._cache[name] = adapter
            if old is not None and old is not adapter:
                await self._safe_close(old, name)
            return adapter

    def get(self, name: str) -> BaseApiAdapter:
        """Возвращает адаптер по имени.

        Args:
            name: Имя источника.

        Returns:
            Адаптер.

        Raises:
            KeyError: Если адаптер не загружен.
        """
        if name not in self._cache:
            raise KeyError(f"Adapter {name} not loaded")
        return self._cache[name]

    def all(self) -> list[BaseApiAdapter]:
        """Все загруженные адаптеры.

        Returns:
            Список адаптеров (снимок на момент вызова).
        """
        return list(self._cache.values())

    async def reload(self) -> None:
        """Полная пересборка кеша. Эквивалентно `load()`."""
        await self.load()

    async def aclose(self) -> None:
        """Закрывает все адаптеры; кеш очищается. Идемпотентно."""
        adapters = list(self._cache.values())
        self._cache.clear()
        await asyncio.gather(
            *(self._safe_close(a, a.source_name) for a in adapters),
            return_exceptions=True,
        )

    @staticmethod
    async def _safe_close(adapter: BaseApiAdapter, name: str) -> None:
        """Закрывает адаптер, логируя ошибки (cleanup не должен падать).

        Args:
            adapter: Адаптер.
            name: Имя источника (для лога).
        """
        try:
            await adapter.close()
        except Exception:  # noqa: BLE001
            logger.exception("adapter_close_failed source=%s", name)