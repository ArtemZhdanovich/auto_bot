"""Выжимка диалога для менеджера/партнёра (12 §3).

Последние ≤50 сообщений треда → LLM-выжимка (≤600 знаков): что хочет
клиент, обсуждаемые лоты, сомнения, договорённости, следующий шаг.
Кэш Redis 1 час по last_message_id — новые сообщения сбрасывают ключ.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.application.catalog.ports import LLMFactory
from app.application.chat.dto import ParticipantRef
from app.application.chat.ports import ChatRepository
from app.application.dialog.use_cases import AiUnavailable
from app.application.errors import NotFound
from app.application.shared.ports import CachePort

logger = logging.getLogger(__name__)

# Последние 50 сообщений (12 §3) и лимит выжимки.
SUMMARY_MESSAGES_LIMIT = 50
SUMMARY_MAX_CHARS = 600
# Кэш 1 час по последнему сообщению (12 §3).
SUMMARY_CACHE_PREFIX = "chat:summary"
SUMMARY_CACHE_TTL_SEC = 60 * 60

CHAT_NOT_FOUND = "chat_not_found: тред не найден"
CHAT_SUMMARY_EMPTY = "chat_summary_empty: в треде нет сообщений"

SUMMARY_SYSTEM_PROMPT = (
    "Ты — ассистент менеджера автосалона. Тебе дают переписку с клиентом. "
    f"Сделай выжимку максимум {SUMMARY_MAX_CHARS} знаков по-русски: что "
    "хочет клиент, какие автомобили/лоты обсуждаются, сомнения клиента, "
    "договорённости и следующий шаг менеджера. Пиши только по переписке, "
    "ничего не выдумывай, без markdown и без перечисления всех сообщений."
)


@dataclass(frozen=True, slots=True)
class ThreadSummaryView:
    """Ответ POST /api/{admin|partner}/chats/{id}/summary (12 §3)."""

    thread_id: uuid.UUID
    summary: str
    last_message_id: int
    generated_at: str


class SummarizeThread:
    """LLM-выжимка треда: доступ → хвост сообщений → кэш → LLM."""

    def __init__(
        self,
        chats: ChatRepository,
        llm_factory: LLMFactory,
        cache: CachePort,
    ) -> None:
        self._chats = chats
        self._llm_factory = llm_factory
        self._cache = cache

    async def execute(
        self,
        thread_id: uuid.UUID,
        requester: ParticipantRef | None = None,
    ) -> ThreadSummaryView:
        """Собрать выжимку треда.

        Args:
            thread_id: Тред.
            requester: Субъект партнёрского контура (участие проверяется);
                `None` — админ-контур (оператор видит все треды, §2).

        Raises:
            NotFound: Треда нет/нет доступа или в нём нет сообщений.
            AiUnavailable: LLM не сконфигурирована или ответила ошибкой.
        """
        if requester is not None:
            if (
                await self._chats.get_participant_thread(
                    thread_id, requester.user_type, requester.user_id
                )
                is None
            ):
                raise NotFound(CHAT_NOT_FOUND)
        elif await self._chats.get_thread(thread_id) is None:
            raise NotFound(CHAT_NOT_FOUND)

        # Хвост переписки (по возрастанию id, новые 50).
        messages = await self._chats.messages(
            thread_id, limit=SUMMARY_MESSAGES_LIMIT
        )
        if not messages:
            raise NotFound(CHAT_SUMMARY_EMPTY)
        last_message_id = messages[-1].id

        cached = await self._cached(thread_id, last_message_id)
        if cached is not None:
            return cached

        transcript = "\n".join(
            f"{m.sender_type}({m.sender_id if m.sender_id is not None else ''}): {m.body}"
            for m in messages
        )
        summary = await self._ask_llm(thread_id, transcript)
        view = ThreadSummaryView(
            thread_id=thread_id,
            summary=summary[:SUMMARY_MAX_CHARS],
            last_message_id=last_message_id,
            generated_at=datetime.now(UTC).isoformat(),
        )
        await self._remember(thread_id, last_message_id, view)
        logger.info(
            "chat_summary_built thread_id=%s requester=%s messages=%d",
            thread_id,
            requester.user_type if requester else "admin",
            len(messages),
        )
        return view

    async def _cached(
        self, thread_id: uuid.UUID, last_message_id: int
    ) -> ThreadSummaryView | None:
        """Кэш 1ч по last_message_id; лежачий Redis не роняет выжимку."""
        try:
            raw = await self._cache.get(
                f"{SUMMARY_CACHE_PREFIX}:{thread_id}:{last_message_id}"
            )
        except Exception:  # noqa: BLE001 — кэш не критичен
            logger.warning("chat_summary_cache_get_failed", exc_info=True)
            return None
        if not raw:
            return None
        try:
            data: dict[str, Any] = json.loads(raw)
            return ThreadSummaryView(
                thread_id=thread_id,
                summary=data["summary"],
                last_message_id=last_message_id,
                generated_at=data["generated_at"],
            )
        except (KeyError, TypeError, ValueError):
            return None

    async def _remember(
        self, thread_id: uuid.UUID, last_message_id: int, view: ThreadSummaryView
    ) -> None:
        """Положить выжимку в Redis на 1 час (best-effort)."""
        try:
            await self._cache.set(
                f"{SUMMARY_CACHE_PREFIX}:{thread_id}:{last_message_id}",
                json.dumps(
                    {"summary": view.summary, "generated_at": view.generated_at},
                    ensure_ascii=False,
                ),
                SUMMARY_CACHE_TTL_SEC,
            )
        except Exception:  # noqa: BLE001 — кэш не критичен
            logger.warning("chat_summary_cache_set_failed", exc_info=True)

    async def _ask_llm(self, thread_id: uuid.UUID, transcript: str) -> str:
        """Выжимка переписки; сбой модели → 503 (фолбэка нет)."""
        try:
            llm = self._llm_factory()
            reply = await llm.complete(
                [
                    {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
                    {"role": "user", "content": transcript},
                ]
            )
        except AiUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001 — единая ошибка наружу
            logger.warning("chat_summary_llm_failed thread_id=%s", thread_id)
            raise AiUnavailable() from exc
        text = str(getattr(reply, "text", "") or "").strip()
        if not text:
            raise AiUnavailable()
        return text
