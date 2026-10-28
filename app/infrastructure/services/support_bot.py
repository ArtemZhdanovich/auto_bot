"""Инфраструктура RAG-бота поддержки: адаптеры RAG/LLM и фоновый спавнер.

- `SupportRagAdapter` — вопрос в RAG-сервис (общий клиент с кэшем,
  retry и circuit breaker); недоступен/пусто/низкий confidence → `None`;
- `SupportLLMAdapter` — один ход LLM по истории треда (OpenAI-формат);
- `SessionSupportBotRunner` — `SupportBotSpawner`: ответ бота выполняется
  в собственной сессии из фабрики. Сессия HTTP-запроса закрывается вместе
  с ответом, а генерация идёт секунды — делить `AsyncSession` между
  фоновым заданием и запросом нельзя (SQLAlchemy: concurrent operations
  are not permitted), поэтому задание открывает свою сессию и UoW.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.chat.bot import ReplySupportBot
from app.config import get_settings
from app.infrastructure.ai.rag_client import (
    RAGUnavailable,
    get_shared_rag_client,
)

logger = logging.getLogger(__name__)

# Ссылки на фоновые задания: без них task может собрать сборщик мусора.
_SPAWNED_TASKS: set[asyncio.Task[None]] = set()


class SupportRagAdapter:
    """`SupportRagPort` → общий RAG-клиент (кэш, retry, circuit breaker).

    Недоступный сервис, пустой ответ или confidence ниже порога → `None` —
    сценарий бота уходит в LLM-фолбэк (как диалог, план RAG фаза 3).
    """

    def __init__(self, redis: Any) -> None:
        self._redis = redis

    async def ask(self, question: str) -> str | None:
        """Ответ базы знаний или `None` (фолбэк)."""
        client = get_shared_rag_client(self._redis)
        if not client.enabled or not question.strip():
            return None
        try:
            answer = await client.ask(
                question, context={"tenant": "autoselect", "scope": "support"}
            )
        except RAGUnavailable:
            logger.warning("support_rag_unavailable")
            return None
        threshold = get_settings().rag_confidence_threshold
        if answer.confidence is not None and answer.confidence < threshold:
            logger.info("support_rag_low_confidence: %.2f", answer.confidence)
            return None
        return (answer.answer or "").strip() or None


class SupportLLMAdapter:
    """`SupportLLMPort` → LLM-клиент (история треда в OpenAI-формате).

    Фабрика вызывается на каждый ход: кэш клиента живёт внутри
    `get_cached_llm_client`, а несконфигурированный провайдер
    (`AiUnavailable`) глотается вместе с прочими сбоями.
    """

    def __init__(self, factory: Callable[[], Any]) -> None:
        self._factory = factory

    async def reply(self, messages: list[dict[str, str]]) -> str | None:
        """Ответ модели или `None` (сбой провайдера не роняет бота)."""
        try:
            result = await self._factory().complete(messages)
        except Exception:  # noqa: BLE001 — фолбэк уже есть (SUPPORT_FALLBACK)
            logger.warning("support_llm_failed", exc_info=True)
            return None
        return (getattr(result, "text", None) or "").strip() or None


class SessionSupportBotRunner:
    """`SupportBotSpawner` на собственной сессии из фабрики.

    Сценарий собирается на месте: репозиторий/профили/UoW привязываются к
    новой сессии задания, публикация и адаптеры переиспользуются (они
    не держат соединений).
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        bot_factory: Callable[[AsyncSession], ReplySupportBot],
    ) -> None:
        self._session_factory = session_factory
        self._bot_factory = bot_factory

    def spawn(self, thread_id: UUID, user_id: int, question: str) -> None:
        """Поставить фоновый ответ; ошибки — только в лог (§6)."""
        task = asyncio.create_task(self._run(thread_id, user_id, question))
        _SPAWNED_TASKS.add(task)
        task.add_done_callback(_SPAWNED_TASKS.discard)

    async def _run(
        self, thread_id: UUID, user_id: int, question: str
    ) -> None:
        """Ответить ботом в собственной сессии (свой commit)."""
        async with self._session_factory() as session:
            try:
                await self._bot_factory(session).execute(
                    thread_id, user_id, question
                )
            except Exception:  # noqa: BLE001 — REST-ответ уже отдан клиенту
                logger.warning(
                    "support_bot_failed thread=%s", thread_id, exc_info=True
                )


__all__ = [
    "SessionSupportBotRunner",
    "SupportLLMAdapter",
    "SupportRagAdapter",
]
