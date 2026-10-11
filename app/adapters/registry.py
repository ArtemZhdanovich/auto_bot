"""AdapterRegistry — динамическая сборка адаптеров из БД (ТЗ п. 6.5).

Код в Git, конфиг в БД: класс берётся из ADAPTER_CLASSES,
параметры — из строки таблицы `sources`. Секреты не хранятся в БД:
`credentials_ref` — имя переменной окружения.
"""

import logging
import os
from typing import Any

from sqlalchemy import select

from app.adapters import ADAPTER_CLASSES
from app.adapters.base import BaseApiAdapter
from app.db.models import Source

logger = logging.getLogger(__name__)


class EnvSecretStore:
    """Простой store секретов: credentials_ref = имя переменной окружения.

    Vault/AWS SM подключаются на этапе 8 (ТЗ п. 11.6, SECRET_STORE).
    """

    async def get(self, ref: str) -> str | None:
        return os.environ.get(ref)


class AdapterRegistry:
    def __init__(
        self,
        session_factory,
        secret_store: Any | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._secrets = secret_store or EnvSecretStore()
        self._cache: dict[str, BaseApiAdapter] = {}

    async def load(self) -> None:
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    select(Source).where(Source.enabled).order_by(Source.priority)
                )
            ).scalars().all()

        self._cache.clear()
        for row in rows:
            cls = ADAPTER_CLASSES.get(row.adapter_class)
            if cls is None:
                logger.error(
                    "adapter_class_not_found: name=%s cls=%s",
                    row.name,
                    row.adapter_class,
                )
                continue
            creds = None
            if row.credentials_ref:
                creds = await self._secrets.get(row.credentials_ref)
            self._cache[row.name] = cls(
                source_name=row.name,
                config=row.config or {},
                credentials=creds,
                rate_limit=row.rate_limit_per_min,
                timeout=row.timeout_sec,
                capabilities=row.capabilities or {},
            )

    def get(self, name: str) -> BaseApiAdapter:
        if name not in self._cache:
            raise KeyError(f"Adapter {name} not loaded")
        return self._cache[name]

    def all(self) -> list[BaseApiAdapter]:
        return list(self._cache.values())

    async def reload(self) -> None:
        self._cache.clear()
        await self.load()

    async def aclose(self) -> None:
        for adapter in self._cache.values():
            await adapter.close()
        self._cache.clear()
