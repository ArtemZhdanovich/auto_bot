"""Тонкий фасад ParserDoctor: сборка портов и запуск сценария.

Триаж причины — `domain/health/triage.py`, шаги ремонта —
`application/parser_doctor/use_cases.py`, SQL — в репозиториях.
Здесь только выбор реализаций и гарантия «врач не роняет фетч».
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.parser_doctor.use_cases import RepairParser, extract_json
from app.application.ports import LLMPort
from app.config import get_settings
from app.domain.health.triage import classify_fetch_error, classify_page
from app.infrastructure.ai.llm_client import build_llm_client
from app.infrastructure.db.repositories.parser_health import SqlAlchemySourceRepository
from app.infrastructure.db.session import session_factory
from app.infrastructure.db.uow import SessionUnitOfWork
from app.infrastructure.locks import RedisLock
from app.infrastructure.services.alerting import TelegramAlertPort
from app.infrastructure.sources.probe import AdapterProbe
from app.infrastructure.sources.registry import AdapterRegistry

logger = logging.getLogger(__name__)

__all__ = [
    "classify_fetch_error",
    "classify_page",
    "extract_json",
    "repair_parser",
]


class _RegistryAdapter:
    """`SourceRegistryPort` поверх `AdapterRegistry`."""

    def __init__(self, registry: AdapterRegistry) -> None:
        self._registry = registry

    async def reload(self, source_name: str) -> None:
        """Пересобрать адаптер источника из нового конфига."""
        await self._registry.reload_source(source_name)


def _llm_factory(llm: LLMPort | None) -> Callable[[], LLMPort]:
    """Ленивая сборка клиента: без LLM-ремонта модель не создаётся вовсе."""

    if llm is not None:
        return lambda: llm

    def build() -> LLMPort:
        return build_llm_client(get_settings())

    return build


async def repair_parser(
    source_name: str,
    adapter: Any,
    registry: AdapterRegistry | None = None,
    *,
    session: AsyncSession | None = None,
    redis: Redis | None = None,
    llm: LLMPort | None = None,
) -> dict[str, Any]:
    """Диагностика и ремонт SSR-парсера источника. Не бросает исключений.

    session/redis/llm — точки инъекции для тестов; в воркере создаются сами.
    """
    own_session = session is None
    session = session or session_factory()
    try:
        use_case = RepairParser(
            SqlAlchemySourceRepository(session),
            AdapterProbe(adapter),
            _llm_factory(llm),
            TelegramAlertPort(redis),
            SessionUnitOfWork(session),
            locks=RedisLock(redis) if redis is not None else None,
            registry=_RegistryAdapter(registry) if registry is not None else None,
        )
        return (await use_case.execute(source_name)).to_payload()
    except Exception as exc:  # noqa: BLE001 — врач не должен ронять фетч
        logger.exception("parser_doctor_failed: %s", source_name)
        return {"source": source_name, "status": "error", "error": str(exc)}
    finally:
        if own_session:
            await session.close()
