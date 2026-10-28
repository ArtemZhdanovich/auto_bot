"""RAG-бот поддержки: авто-ответ на сообщения юзера в support-треде (§6).

Механика (05-chats-marketplace §6, §10):
- вызывается фоном (`asyncio.create_task`) после успешного POST messages —
  REST не ждёт LLM, ответ приедет по сокету/поллингу;
- `bot_paused` (оператор писал последним) → бот молчит; флаг снимает
  репозиторий в `add_message`, когда юзер пишет после оператора (§2);
- Free — 15 сообщений/день (скользящие сутки): сверх лимита бот пишет
  апселл вместо ответа, без 403 — чат не должен «ломаться»;
- ответ: сначала RAG-сервис; пусто/ошибка/низкий confidence → LLM-диалог
  по короткой истории треда (последние 10 сообщений, как в `dialog`);
- перед генерацией в канал публикуется typing-событие (§10: RAG-лаг).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID

from app.application.catalog.ports import UserLookupPort
from app.application.chat.dto import (
    MessageView,
    ParticipantRef,
    SendMessageCommand,
)
from app.application.chat.ports import ChatRepository, ThreadEventPublisher
from app.application.chat.use_cases import message_event
from app.application.shared.ports import UnitOfWork

logger = logging.getLogger(__name__)

# Суточный лимит Free в support-треде (§6); pro/business — без лимита.
SUPPORT_FREE_DAILY_LIMIT = 15
# Скользящее окно лимита.
SUPPORT_WINDOW = timedelta(hours=24)
# Короткая история треда для LLM-фолбэка.
SUPPORT_HISTORY_MESSAGES = 10

# Апселл при исчерпании Free-лимита: системное сообщение вместо ответа (§6).
SUPPORT_UPSELL = (
    "Лимит бесплатных вопросов поддержки на сегодня исчерпан "
    f"({SUPPORT_FREE_DAILY_LIMIT} в сутки). Перейдите на Pro или Business — "
    "и бот отвечает без ограничений."
)
# RAG и LLM оба недоступны — честный ответ вместо молчания.
SUPPORT_FALLBACK = (
    "Не удалось собрать ответ — передал вопрос оператору, человек "
    "подключится к этому чату."
)
SUPPORT_SYSTEM_PROMPT = (
    "Ты — вежливый оператор поддержки сервиса подбора автомобилей из "
    "Кореи. Отвечай кратко и по делу, на русском языке. Если не знаешь "
    "ответа — честно скажи и предложи позвать живого оператора."
)


class SupportRagPort(Protocol):
    """Вопрос в базу знаний RAG; `None` — пусто/недоступен/низкий confidence."""

    async def ask(self, question: str) -> str | None: ...


class SupportLLMPort(Protocol):
    """Один ход LLM-диалога по истории треда; `None` — LLM недоступна."""

    async def reply(self, messages: list[dict[str, str]]) -> str | None: ...


class SupportBotSpawner(Protocol):
    """Постановка фонового ответа бота после сообщения юзера.

    Реализация обязана выполняться вне сессии HTTP-запроса: генерация
    занимает секунды (RAG/LLM), а REQUEST-скоуп закрывается вместе с
    ответом. Ошибки логируются, не пробрасываются.
    """

    def spawn(self, thread_id: UUID, user_id: int, question: str) -> None: ...


def support_daily_limit(tier: str) -> int | None:
    """Суточный лимит сообщений бота по тарифу (`None` — безлимит, §6)."""
    return SUPPORT_FREE_DAILY_LIMIT if tier == "free" else None


def _bot_sender() -> ParticipantRef:
    """Отправитель-бот (user_id не пишется, §3: sender_id NULL)."""
    return ParticipantRef(user_type="bot", user_id=0)


class ReplySupportBot:
    """Ответить ботом на сообщение юзера в support-треде (§6).

    Молчит (возвращает `None`, ничего не публикуя), если тред не support,
    закрыт или `bot_paused`; не-поддержка и чужие треды отсеиваются тем
    же доступом, что и REST (`get_participant_thread`).
    """

    def __init__(
        self,
        chats: ChatRepository,
        users: UserLookupPort,
        publisher: ThreadEventPublisher,
        rag: SupportRagPort,
        llm: SupportLLMPort,
        uow: UnitOfWork,
    ) -> None:
        self._chats = chats
        self._users = users
        self._publisher = publisher
        self._rag = rag
        self._llm = llm
        self._uow = uow

    async def execute(
        self, thread_id: UUID, user_id: int, question: str
    ) -> MessageView | None:
        """Сгенерировать и записать ответ бота.

        Args:
            thread_id: Тред, куда юзер только что написал сообщение.
            user_id: telegram_id автора вопроса.
            question: Текст сообщения юзера.

        Returns:
            Сообщение бота или `None` (не support-тред / бот на паузе).
        """
        thread = await self._chats.get_participant_thread(
            thread_id, "user", user_id
        )
        if thread is None or thread.type != "support":
            return None
        if thread.status != "active":
            return None
        if thread.bot_paused:
            # Оператор писал последним — бот не вмешивается, пока юзер
            # снова не напишет (флаг снимает add_message, §2/§6).
            logger.info("support_bot_paused thread=%s", thread_id)
            return None

        limit = await self._daily_limit(thread.user_id)
        if limit is not None:
            used = await self._chats.count_messages(
                thread_id,
                sender_type="user",
                since=datetime.now(UTC) - SUPPORT_WINDOW,
            )
            if used > limit:
                # Апселл вместо ответа, без 403: чат остаётся рабочим (§6).
                logger.info(
                    "support_bot_limit_hit user_id=%s used=%s limit=%s",
                    thread.user_id,
                    used,
                    limit,
                )
                return await self._say(
                    thread_id, thread.user_id, SUPPORT_UPSELL
                )

        # §10: RAG-лаг — до генерации показываем «бот печатает»
        # (09 §1: личные каналы участников, payload тот же).
        await self._publisher.publish_to_participants(
            thread_id, {"type": "typing", "user_type": "bot"}
        )
        answer = await self._answer(thread_id, question)
        return await self._say(thread_id, thread.user_id, answer)

    async def _daily_limit(self, user_id: int) -> int | None:
        """Лимит по тарифу владельца треда (нет профиля → безопасный free)."""
        profile = await self._users.get(user_id)
        tier = profile.tier if profile is not None else "free"
        return support_daily_limit(tier)

    async def _answer(self, thread_id: UUID, question: str) -> str:
        """RAG → пусто/ошибка → LLM по истории треда → честный фолбэк."""
        try:
            rag_answer = await self._rag.ask(question)
        except Exception:  # noqa: BLE001 — RAG не должен ронять бота
            logger.warning(
                "support_rag_failed thread=%s", thread_id, exc_info=True
            )
            rag_answer = None
        if rag_answer:
            return rag_answer

        try:
            llm_answer = await self._llm.reply(
                await self._llm_messages(thread_id, question)
            )
        except Exception:  # noqa: BLE001 — и LLM не должна ронять бота
            logger.warning(
                "support_llm_failed thread=%s", thread_id, exc_info=True
            )
            llm_answer = None
        return llm_answer or SUPPORT_FALLBACK

    async def _llm_messages(
        self, thread_id: UUID, question: str
    ) -> list[dict[str, str]]:
        """История треда в OpenAI-формате: последние 10 сообщений + вопрос.

        System-приветствия пропускаются (не ход диалога); оператор/партнёр
        говорят от лица ассистента — для поддержки это и есть «наша сторона».
        """
        history = await self._chats.messages(
            thread_id, limit=SUPPORT_HISTORY_MESSAGES
        )
        messages: list[dict[str, str]] = [
            {"role": "system", "content": SUPPORT_SYSTEM_PROMPT}
        ]
        for item in history:
            if item.sender_type == "system":
                continue
            role = "user" if item.sender_type == "user" else "assistant"
            messages.append({"role": role, "content": item.body})
        # Вопрос уже сохранён в треде, но гарантируем его наличие последним
        # ходом — сценарий вызывают и напрямую, без записи сообщения.
        last = messages[-1]
        if not (last["role"] == "user" and last["content"] == question):
            messages.append({"role": "user", "content": question})
        return messages

    async def _say(
        self, thread_id: UUID, user_id: int, body: str
    ) -> MessageView:
        """Записать сообщение бота и опубликовать событие (§4, 09 §1).

        Полный MessageView уходит в личные каналы участников — отдельного
        уведомления user:{id} больше не нужно (личный канал и есть канал).
        """
        message = await self._chats.add_message(
            SendMessageCommand(
                thread_id=thread_id, sender=_bot_sender(), body=body
            )
        )
        await self._uow.commit()
        await self._publisher.publish_to_participants(
            thread_id, message_event(thread_id, message)
        )
        return message


__all__ = [
    "SUPPORT_FALLBACK",
    "SUPPORT_FREE_DAILY_LIMIT",
    "SUPPORT_HISTORY_MESSAGES",
    "SUPPORT_SYSTEM_PROMPT",
    "SUPPORT_UPSELL",
    "SUPPORT_WINDOW",
    "ReplySupportBot",
    "SupportBotSpawner",
    "SupportLLMPort",
    "SupportRagPort",
    "support_daily_limit",
]
