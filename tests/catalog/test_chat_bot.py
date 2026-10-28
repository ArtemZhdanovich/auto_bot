"""RAG-бот поддержки (B2 §6): ответ, пауза, лимит Free, LLM-фолбэк.

Юнит-тесты на фейковых репозиториях (без БД): сценарий `ReplySupportBot`
проверяется целиком — сохранение сообщения, публикация typing/message,
апселл при исчерпании лимита и фолбэк на LLM при пустом RAG.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from app.application.chat.bot import (
    SUPPORT_FALLBACK,
    SUPPORT_FREE_DAILY_LIMIT,
    SUPPORT_HISTORY_MESSAGES,
    SUPPORT_UPSELL,
    ReplySupportBot,
)
from app.application.chat.publisher import ParticipantEventPublisher
from app.application.chat.dto import (
    MessageView,
    ParticipantRef,
    SendMessageCommand,
    ThreadRecord,
)
from app.application.dialog.dto import UserProfile

pytestmark = pytest.mark.asyncio

USER_ID = 900100
THREAD_ID = UUID("00000000-0000-0000-0000-0000000000aa")


# =========================================================================
# Фейки портов (по образцу B1-тестов чатов)
# =========================================================================


class FakeChatRepo:
    """ChatRepository в памяти: треды и сообщения, bot_paused как в SQL."""

    def __init__(self, threads: dict[UUID, ThreadRecord] | None = None) -> None:
        self.threads: dict[UUID, ThreadRecord] = dict(threads or {})
        # ThreadRecord immutable — bot_paused/last_message живёт отдельно.
        self.paused: dict[UUID, bool] = {tid: t.bot_paused for tid, t in self.threads.items()}
        self.store: dict[UUID, list[MessageView]] = defaultdict(list)
        self._next_id = 1

    async def get_participant_thread(
        self, thread_id: UUID, user_type: str, user_id: int
    ) -> ThreadRecord | None:
        thread = self.threads.get(thread_id)
        if thread is None or user_type != "user":
            return None
        if thread.user_id != user_id:
            return None
        if self.paused.get(thread_id):
            return replace(thread, bot_paused=True)
        return thread

    async def add_message(self, command: SendMessageCommand) -> MessageView:
        sender = command.sender
        message = MessageView(
            id=self._next_id,
            sender_type=sender.user_type,
            sender_id=None
            if sender.user_type in ("system", "bot")
            else sender.user_id,
            body=command.body,
            created_at=datetime.now(UTC),
        )
        self._next_id += 1
        self.store[command.thread_id].append(message)
        if command.thread_id in self.paused:
            # Как в SqlAlchemyChatRepository: operator ставит паузу,
            # сообщение юзера снимает (§2/§6).
            if sender.user_type == "operator":
                self.paused[command.thread_id] = True
            elif sender.user_type == "user":
                self.paused[command.thread_id] = False
        return message

    async def count_messages(
        self, thread_id: UUID, *, sender_type: str, since: datetime
    ) -> int:
        return sum(
            1
            for m in self.store[thread_id]
            if m.sender_type == sender_type and m.created_at >= since
        )

    async def messages(
        self,
        thread_id: UUID,
        *,
        before_id: int | None = None,
        after_id: int | None = None,
        limit: int = 50,
    ) -> list[MessageView]:
        items = self.store[thread_id]
        if after_id is not None:
            items = [m for m in items if m.id > after_id]
        elif before_id is not None:
            items = [m for m in items if m.id < before_id]
        return list(items[-limit:])

    async def get_thread(self, thread_id: UUID) -> ThreadRecord | None:
        # 09 §1: publisher участников читает запись треда (owner/partner).
        return self.threads.get(thread_id)

    async def list_participants(self, thread_id: UUID) -> list:
        # Support-тредов без строк участников: владелец — единственный.
        return []

    # Не используется ботом — заглушки для полноты порта.
    async def list_threads_for_user(self, user_id: int):
        return []

    async def list_threads_for_partner(self, partner_id: int):
        return []


class FakePublisher:
    """ChatPublisher: пишет (канал, payload) в список (09 §1 — личные)."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    async def publish(self, channel: str, data: dict) -> None:
        self.events.append((channel, data))

    def channel_events(self, channel: str) -> list[dict]:
        """События одного канала (личный канал владельца треда)."""
        return [data for ch, data in self.events if ch == channel]

    def thread_events(self, thread_id: UUID) -> list[dict]:
        """События личного канала владельца support-треда (USER_ID)."""
        return self.channel_events(f"user:{USER_ID}")


class FakeUsers:
    """UserLookupPort с настраиваемым тарифом."""

    def __init__(self, tier: str = "free") -> None:
        self.tier = tier

    async def get(self, user_id: int) -> UserProfile | None:
        return UserProfile(user_id=user_id, is_active=True, tier=self.tier)


class FakeRag:
    """SupportRagPort: заготовленный ответ или исключение."""

    def __init__(self, answer: str | None = None, error: Exception | None = None) -> None:
        self.answer = answer
        self.error = error
        self.calls: list[str] = []

    async def ask(self, question: str) -> str | None:
        self.calls.append(question)
        if self.error is not None:
            raise self.error
        return self.answer


class FakeLLM:
    """SupportLLMPort: заготовленный ответ; запоминает ходы."""

    def __init__(self, answer: str | None = None, error: Exception | None = None) -> None:
        self.answer = answer
        self.error = error
        self.turns: list[list[dict[str, str]]] = []

    async def reply(self, messages: list[dict[str, str]]) -> str | None:
        self.turns.append(messages)
        if self.error is not None:
            raise self.error
        return self.answer


class FakeUoW:
    """UnitOfWork-заглушка: считает коммиты, поддерживает async with."""

    def __init__(self) -> None:
        self.commits = 0

    async def __aenter__(self) -> "FakeUoW":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        return None


def _support_thread(
    *,
    type: str = "support",  # noqa: A002 — имя поля ThreadRecord
    status: str = "active",
    bot_paused: bool = False,
) -> ThreadRecord:
    """Активный тред владельца USER_ID заданного типа."""
    return ThreadRecord(
        id=THREAD_ID,
        type=type,
        user_id=USER_ID,
        status=status,
        bot_paused=bot_paused,
    )


def _bot(
    repo: FakeChatRepo,
    *,
    tier: str = "free",
    rag: FakeRag | None = None,
    llm: FakeLLM | None = None,
    uow: FakeUoW | None = None,
    publisher: FakePublisher | None = None,
) -> tuple[ReplySupportBot, FakePublisher, FakeUoW]:
    """Собрать сценарий на фейках; вернуть (бот, паблишер, uow)."""
    publisher = publisher or FakePublisher()
    uow = uow or FakeUoW()
    bot = ReplySupportBot(
        chats=repo,  # type: ignore[arg-type]
        users=FakeUsers(tier),  # type: ignore[arg-type]
        # 09 §1: события треда идут в личные каналы участников.
        publisher=ParticipantEventPublisher(repo, publisher),  # type: ignore[arg-type]
        rag=rag or FakeRag(),  # type: ignore[arg-type]
        llm=llm or FakeLLM(),  # type: ignore[arg-type]
        uow=uow,  # type: ignore[arg-type]
    )
    return bot, publisher, uow


# =========================================================================
# Ответ бота
# =========================================================================


async def test_bot_answers_from_rag_and_publishes():
    """RAG ответил → сообщение bot сохранено, typing и message опубликованы."""
    repo = FakeChatRepo({THREAD_ID: _support_thread()})
    rag = FakeRag(answer="Доставка из Кореи занимает 25–40 дней.")
    bot, publisher, uow = _bot(repo, rag=rag)

    reply = await bot.execute(THREAD_ID, USER_ID, "Сколько идёт доставка?")

    assert reply is not None and reply.sender_type == "bot"
    assert reply.body == "Доставка из Кореи занимает 25–40 дней."
    assert repo.store[THREAD_ID][-1] == reply
    assert uow.commits == 1
    assert rag.calls == ["Сколько идёт доставка?"]

    # 09 §1: всё уходит в личный канал владельца, thread:* каналов нет
    assert all(
        not ch.startswith("thread:") for ch, _ in publisher.events
    )
    events = publisher.thread_events(THREAD_ID)
    assert events[0]["type"] == "typing" and events[0]["user_type"] == "bot"
    assert events[0]["thread_id"] == str(THREAD_ID)
    assert events[-1]["type"] == "message" and events[-1]["id"] == reply.id


async def test_bot_silent_when_paused():
    """bot_paused (оператор писал последним) → бот молчит, ничего не пишет."""
    thread = _support_thread(bot_paused=True)
    repo = FakeChatRepo({THREAD_ID: thread})
    repo.paused[THREAD_ID] = True
    rag = FakeRag(answer="не должен быть вызван")
    bot, publisher, uow = _bot(repo, rag=rag)

    reply = await bot.execute(THREAD_ID, USER_ID, "Ещё вопрос")

    assert reply is None
    assert repo.store[THREAD_ID] == []
    assert publisher.events == []
    assert rag.calls == []
    assert uow.commits == 0


async def test_bot_resumes_after_user_message():
    """Юзер пишет после оператора → add_message снимает паузу, бот отвечает."""
    repo = FakeChatRepo({THREAD_ID: _support_thread()})
    repo.paused[THREAD_ID] = False
    # Оператор вклинился → пауза (как делает SQL-репозиторий).
    await repo.add_message(
        SendMessageCommand(
            thread_id=THREAD_ID,
            sender=ParticipantRef(user_type="operator", user_id=1),
            body="Здравствуйте, я оператор",
        )
    )
    assert repo.paused[THREAD_ID] is True
    # Юзер пишет → пауза снята, бот отвечает.
    await repo.add_message(
        SendMessageCommand(
            thread_id=THREAD_ID,
            sender=ParticipantRef(user_type="user", user_id=USER_ID),
            body="Спасибо, вопрос остался",
        )
    )
    assert repo.paused[THREAD_ID] is False
    bot, publisher, _ = _bot(repo, rag=FakeRag(answer="Отвечаю"))
    reply = await bot.execute(THREAD_ID, USER_ID, "Спасибо, вопрос остался")
    assert reply is not None and reply.body == "Отвечаю"
    assert publisher.thread_events(THREAD_ID)


async def test_free_limit_upsell_after_15_messages():
    """Free: 16-е сообщение за сутки → апселл вместо ответа, без RAG."""
    repo = FakeChatRepo({THREAD_ID: _support_thread()})
    now = datetime.now(UTC)
    for i in range(SUPPORT_FREE_DAILY_LIMIT):
        repo.store[THREAD_ID].append(
            MessageView(
                id=i + 1,
                sender_type="user",
                sender_id=USER_ID,
                body=f"вопрос {i + 1}",
                created_at=now - timedelta(minutes=10),
            )
        )
    # 16-е сообщение юзера уже сохранено (как это делает SendMessage).
    await repo.add_message(
        SendMessageCommand(
            thread_id=THREAD_ID,
            sender=ParticipantRef(user_type="user", user_id=USER_ID),
            body="вопрос 16",
        )
    )
    rag = FakeRag(answer="не должен быть вызван")
    bot, publisher, _ = _bot(repo, rag=rag)

    reply = await bot.execute(THREAD_ID, USER_ID, "вопрос 16")

    assert reply is not None and reply.sender_type == "bot"
    assert reply.body == SUPPORT_UPSELL
    assert "15" in reply.body and "Pro" in reply.body
    assert rag.calls == []  # генерации не было — только апселл
    # Апселл тоже доезжает клиенту событием message (не 403).
    assert publisher.thread_events(THREAD_ID)[-1]["type"] == "message"


async def test_paid_tier_has_no_daily_limit():
    """Pro/Business — без лимита: 50-е сообщение получает ответ бота."""
    repo = FakeChatRepo({THREAD_ID: _support_thread()})
    now = datetime.now(UTC)
    for i in range(50):
        repo.store[THREAD_ID].append(
            MessageView(
                id=i + 1,
                sender_type="user",
                sender_id=USER_ID,
                body=f"вопрос {i + 1}",
                created_at=now - timedelta(minutes=5),
            )
        )
    bot, _, _ = _bot(repo, tier="pro", rag=FakeRag(answer="ответ"))
    reply = await bot.execute(THREAD_ID, USER_ID, "вопрос 51")
    assert reply is not None and reply.body == "ответ"


async def test_limit_window_is_24_hours():
    """Старые сообщения (вне окна 24ч) лимит не съедают."""
    repo = FakeChatRepo({THREAD_ID: _support_thread()})
    repo.store[THREAD_ID].append(
        MessageView(
            id=1,
            sender_type="user",
            sender_id=USER_ID,
            body="вчерашний вопрос",
            created_at=datetime.now(UTC) - timedelta(hours=25),
        )
    )
    bot, _, _ = _bot(repo, rag=FakeRag(answer="ответ"))
    reply = await bot.execute(THREAD_ID, USER_ID, "новый вопрос")
    assert reply is not None and reply.body == "ответ"


# =========================================================================
# LLM-фолбэк
# =========================================================================


async def test_rag_empty_falls_back_to_llm_history():
    """RAG пусто → LLM по истории треда (system + последние ходы + вопрос)."""
    repo = FakeChatRepo({THREAD_ID: _support_thread()})
    # История: приветствие (system), вопрос юзера, ответ оператора.
    history = [
        MessageView(id=1, sender_type="system", sender_id=None,
                    body="Задайте вопрос", created_at=datetime.now(UTC)),
        MessageView(id=2, sender_type="user", sender_id=USER_ID,
                    body="Как оформить сделку?", created_at=datetime.now(UTC)),
        MessageView(id=3, sender_type="operator", sender_id=7,
                    body="Помогу с документами", created_at=datetime.now(UTC)),
    ]
    repo.store[THREAD_ID].extend(history)
    llm = FakeLLM(answer="Соберите документы и договор.")
    bot, _, _ = _bot(repo, rag=FakeRag(answer=None), llm=llm)

    reply = await bot.execute(THREAD_ID, USER_ID, "Как оформить сделку?")

    assert reply is not None and reply.body == "Соберите документы и договор."
    (turn,) = llm.turns
    assert turn[0]["role"] == "system"
    # system-приветствие в ходы не попадает; оператор говорит ассистентом.
    roles = [(m["role"], m["content"]) for m in turn[1:]]
    assert ("user", "Как оформить сделку?") in roles
    assert ("assistant", "Помогу с документами") in roles
    assert all(c != "Задайте вопрос" for _, c in roles)


async def test_rag_error_falls_back_to_llm():
    """RAG упал с исключением → LLM-фолбэк, бот не падает."""
    repo = FakeChatRepo({THREAD_ID: _support_thread()})
    llm = FakeLLM(answer="LLM-ответ")
    bot, _, _ = _bot(
        repo, rag=FakeRag(error=RuntimeError("rag down")), llm=llm
    )
    reply = await bot.execute(THREAD_ID, USER_ID, "вопрос")
    assert reply is not None and reply.body == "LLM-ответ"
    assert len(llm.turns) == 1


async def test_llm_history_capped_to_last_10():
    """История для LLM — последние 10 сообщений треда (короткая память)."""
    repo = FakeChatRepo({THREAD_ID: _support_thread()})
    now = datetime.now(UTC)
    for i in range(30):
        repo.store[THREAD_ID].append(
            MessageView(
                id=i + 1,
                sender_type="user",
                sender_id=USER_ID,
                body=f"msg {i + 1}",
                created_at=now,
            )
        )
    llm = FakeLLM(answer="ok")
    # pro: длинная история не должна упираться в free-лимит 15/день.
    bot, _, _ = _bot(repo, tier="pro", rag=FakeRag(answer=None), llm=llm)
    await bot.execute(THREAD_ID, USER_ID, "msg 30")
    (turn,) = llm.turns
    # system + 10 сообщений истории (вопрос msg 30 уже последним ходом).
    assert len(turn) <= 1 + SUPPORT_HISTORY_MESSAGES


async def test_both_unavailable_polite_fallback():
    """RAG и LLM оба недоступны → честное сообщение, бот не молчит."""
    repo = FakeChatRepo({THREAD_ID: _support_thread()})
    bot, _, _ = _bot(
        repo,
        rag=FakeRag(error=RuntimeError("rag down")),
        llm=FakeLLM(error=RuntimeError("llm down")),
    )
    reply = await bot.execute(THREAD_ID, USER_ID, "вопрос")
    assert reply is not None and reply.body == SUPPORT_FALLBACK


# =========================================================================
# Отсечение не-support тредов
# =========================================================================


async def test_non_support_thread_skipped():
    """Seller/deal-треды и чужие треды бот не трогает."""
    repo = FakeChatRepo({THREAD_ID: _support_thread(type="seller")})
    rag = FakeRag()
    bot, publisher, uow = _bot(repo, rag=rag)
    assert await bot.execute(THREAD_ID, USER_ID, "торг?") is None
    # Чужой тред (не владелец) — тоже молчание.
    repo2 = FakeChatRepo({THREAD_ID: _support_thread()})
    bot2, _, _ = _bot(repo2, rag=FakeRag())
    assert await bot2.execute(THREAD_ID, USER_ID + 1, "вопрос") is None
    assert publisher.events == [] and rag.calls == [] and uow.commits == 0


async def test_closed_thread_skipped():
    """closed → read-only, бот не отвечает."""
    repo = FakeChatRepo({THREAD_ID: _support_thread(status="closed")})
    bot, publisher, _ = _bot(repo, rag=FakeRag(answer="ответ"))
    assert await bot.execute(THREAD_ID, USER_ID, "вопрос") is None
    assert publisher.events == []
