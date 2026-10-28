"""Сценарии чатов: треды, сообщения, read-маркеры, ws-токены, subscribe-proxy.

Правила доступа (§2): юзер пишет/читает свои треды, пока status=active
(closed → read-only); партнёр — seller/deal-треды своего счёта. Публикация
в centrifugo — fire-and-forget: сбой пуша не ломает REST-ответ (клиент
доедет поллингом по after_id, §4).
"""

from __future__ import annotations

import asyncio
import logging
import re
import secrets
from collections.abc import Sequence
from typing import Any
from uuid import UUID

from app.application.catalog.ports import ListingReadRepository
from app.application.catalog.dto import ListingCard
from app.application.chat.dto import (
    MAX_MESSAGE_LEN,
    WS_TOKEN_TTL_SEC,
    MarkReadCommand,
    MessageView,
    OperatorThreadView,
    ParticipantRef,
    SendMessageCommand,
    ThreadRecord,
    ThreadView,
    WsTokenView,
)
from app.application.chat.ports import (
    ChannelPresencePort,
    ChatRepository,
    ThreadEventPublisher,
    WsTokenFactory,
)
from app.application.chat.privacy import mask_contacts
from app.application.errors import Conflict, Forbidden, InvalidInput, NotFound
from app.application.market.ports import MarketLotRepository
from app.application.shared.ports import CachePort, UnitOfWork

logger = logging.getLogger(__name__)

# Системные приветствия (§6): пишутся при создании треда.
SUPPORT_GREETING = (
    "Задайте вопрос — отвечу сразу, человек подключится при необходимости"
)
SELLER_GREETING = (
    "Чат с продавцом создан. Задайте вопрос по автомобилю — ответ придёт сюда."
)
# Внешние лоты (encar/kcar, owner_type IS NULL) продавца не имеют: чат с
# «продавцом-парсером» экономически бессмысленен (06 §5).
MARKET_LOT_REQUIRED = (
    "market_lot_required: чат с продавцом доступен только для лотов рынка"
)
# System-строка при зове оператора (§6): бот на паузе, ждём человека.
SUMMON_OPERATOR_MESSAGE = "Пользователь позвал оператора — бот на паузе"
# Не-support тред оператора не зовёт: RAG-бот живёт только в поддержке (§6).
NOT_A_SUPPORT_THREAD = (
    "not_support_thread: оператора можно позвать только в чате поддержки"
)
# Тред закрыт (архив, 08 §2): отправка недоступна.
THREAD_ARCHIVED = "thread_archived: тред архивирован, отправка недоступна"
# Правила комнаты (08 §3): system-строка при создании переговорной.
GROUP_RULES_MESSAGE = (
    "Правила комнаты: обмен телефонами и внешними мессенджерами скрыт — "
    "обмен контактами только через сделку; после закрытия комната хранится "
    "90 дней; о нарушениях жалуйтесь (⋯ → Пожаловаться)"
)

# Маркдаун-разметка не показывается в списке тредов: плоский текст.
_MD_MARKUP_RE = re.compile(r"[*_`~>\[\]#]")


def plain_text(body: str) -> str:
    """Срезать маркдаун-разметку для превью в списке тредов."""
    return _MD_MARKUP_RE.sub("", body).strip()


def _system_sender() -> ParticipantRef:
    """System-отправитель (user_id=0 — служебный маркер)."""
    return ParticipantRef(user_type="system", user_id=0)


def _operator_sender() -> ParticipantRef:
    """Отправитель-оператор.

    Админ-контур авторизуется X-Internal-Token (без персонального
    identity, MVP) — пишем служебный id=0; user_id появится вместе с
    таблицей admin_users. Репозиторий по user_type='operator' ставит
    `bot_paused=True` (§2/§6).
    """
    return ParticipantRef(user_type="operator", user_id=0)


def _is_thread_owner(thread: ThreadRecord, subject: ParticipantRef) -> bool:
    """Неявный владелец треда (строки участника может не быть, 08 §2).

    user — владелец по `chat_threads.user_id`, partner — по partner_id
    (service-треды и создатель переговорной до первой записи маркера).
    """
    if subject.user_type == "user":
        return thread.user_id == subject.user_id
    if subject.user_type == "partner":
        return thread.partner_id == subject.user_id
    return False


async def _actor_role(
    chats: ChatRepository, thread: ThreadRecord, subject: ParticipantRef
) -> str:
    """Роль субъекта: строка участника, иначе владелец треда, иначе member.

    Говернанс-проверки 08 §2 (invite/kick/leave/archive) — единая точка.
    """
    role = await chats.participant_role(
        thread.id, subject.user_type, subject.user_id
    )
    if role is not None:
        return role
    return "owner" if _is_thread_owner(thread, subject) else "member"


def _validated_body(body: str) -> str:
    """Общий формат сообщения: trim + проверки пустоты и длины (§3)."""
    body = body.strip()
    if not body:
        raise InvalidInput("empty_message: сообщение не может быть пустым")
    if len(body) > MAX_MESSAGE_LEN:
        raise InvalidInput(
            f"message_too_long: максимум {MAX_MESSAGE_LEN} символов"
        )
    return body


class ListMyThreads:
    """GET /api/chats — мои треды всех типов (§5)."""

    def __init__(self, chats: ChatRepository) -> None:
        self._chats = chats

    async def execute(self, user_id: int) -> list[ThreadView]:
        """Треды юзера: владение или участие, с unread и предметом."""
        return await self._chats.list_threads_for_user(user_id)


class ListPartnerThreads:
    """GET /api/partner/chats — seller/deal-треды счёта (§5)."""

    def __init__(self, chats: ChatRepository) -> None:
        self._chats = chats

    async def execute(self, partner_id: int) -> list[ThreadView]:
        """Треды партнёра с unread по участнику-партнёру."""
        return await self._chats.list_threads_for_partner(partner_id)


# =========================================================================
# Переговорные чаты до 5 участников (07 §2)
# =========================================================================

# Лимит участников INCLUDING создателя (строка участника создаётся и для
# создателя — все участники group-треда перечислены в chat_participants).
GROUP_MAX_PARTICIPANTS = 5

# Название переговорной: 3-120 символов (схема + сценарий).
GROUP_TITLE_MIN = 3
GROUP_TITLE_MAX = 120

# Инвайт-код: 6 символов base32 без похожих глифов (0/1/I/L/O/U).
INVITE_CODE_LEN = 6
INVITE_ALPHABET = "23456789ABCDEFGHJKMNPQRSTVWXYZ"

# TTL инвайта в Redis: 7 дней, многоразовый до лимита (07 §2).
INVITE_TTL_SEC = 7 * 24 * 60 * 60

# Попытки подобрать свободный код (коллизия в SET NX).
_INVITE_CODE_ATTEMPTS = 5

GROUP_TITLE_INVALID = (
    f"group_title_invalid: название переговорной — {GROUP_TITLE_MIN}-"
    f"{GROUP_TITLE_MAX} символов"
)
GROUP_IS_FULL = (
    f"group_is_full: в переговорной уже {GROUP_MAX_PARTICIPANTS} участников"
)
INVITE_NOT_FOUND = "invite_not_found: код истёк или не существует"
NOT_A_GROUP_THREAD = (
    "not_group_thread: действие доступно только в переговорной"
)
CHAT_NOT_FOUND = "chat_not_found: тред не найден"
# Говернанс (08 §2): приглашать может только владелец переговорной.
INVITE_OWNER_ONLY = (
    "invite_owner_only: приглашать участников может только владелец"
)
# Привязка к сделке (08 §1): переговорная создаётся только по автомобилю.
GROUP_NEEDS_LISTING = (
    "group_needs_listing: переговорная создаётся только по автомобилю — "
    "укажите listing_id"
)
LISTING_NOT_ACTIVE = (
    "listing_not_active: лот снят с продажи — переговорная по нему "
    "не создаётся"
)


def _auto_group_title(card: ListingCard) -> str:
    """Автоген названия (08 §1): «Переговоры: {make} {model} {year}»."""
    parts = " ".join(
        str(part)
        for part in (card.make, card.model, card.year)
        if part
    )
    title = f"Переговоры: {parts}" if parts else "Переговоры"
    return title[:GROUP_TITLE_MAX]


def _invite_redis_key(code: str) -> str:
    """Ключ Redis инвайта: chat:invite:{code} → thread_id."""
    return f"chat:invite:{code}"


class CreateGroupThread:
    """POST /api/chats/group — переговорная до 5 человек (07 §2, 08 §1).

    Сделко-ориентированная: `listing_id` обязателен (без него — 409
    `group_needs_listing`), title пуст → автоген «Переговоры: Make Model
    Year». Создатель — участник с ролью 'owner' (08 §2); партнёр-владелец
    лота НЕ добавляется автоматически — только по инвайту. Advisory-lock
    не нужен: тред создаёт один субъект, участники появляются через
    invite/join. Существующие group-треды без листинга не трогаем —
    сообщения в них ходят как раньше.
    """

    def __init__(
        self,
        chats: ChatRepository,
        listings: ListingReadRepository,
        uow: UnitOfWork,
    ) -> None:
        self._chats = chats
        self._listings = listings
        self._uow = uow

    async def execute(
        self, user_id: int, title: str, listing_id: int | None = None
    ) -> UUID:
        """Создать тред по лоту; вернуть id.

        Raises:
            Conflict: Лот не указан/не найден (group_needs_listing),
                снят с продажи (listing_not_active).
            InvalidInput: Явный title вне 3-120 символов.
        """
        # §1: переговорная создаётся только по автомобилю.
        if listing_id is None:
            raise Conflict(GROUP_NEEDS_LISTING)
        card = await self._listings.get_active(listing_id)
        if card is None:
            # get_active не различает «нет» и «неактивен» — exists() даёт
            # понятную ошибку для снятого с продажи лота.
            if await self._listings.exists(listing_id):
                raise Conflict(LISTING_NOT_ACTIVE)
            raise Conflict(GROUP_NEEDS_LISTING)
        title = title.strip()
        if not title:
            # §1: пустой title — автогенерация по карточке лота.
            title = _auto_group_title(card)
        if not (GROUP_TITLE_MIN <= len(title) <= GROUP_TITLE_MAX):
            raise InvalidInput(GROUP_TITLE_INVALID)
        thread = await self._chats.create_group_thread(
            user_id=user_id, title=title, listing_id=listing_id
        )
        # §2: создатель — owner (инвайты/кик/архив).
        await self._chats.ensure_participant(
            thread.id, "user", user_id, role="owner"
        )
        # §3: правила комнаты — system-строка при создании.
        await self._chats.add_message(
            SendMessageCommand(
                thread_id=thread.id,
                sender=_system_sender(),
                body=GROUP_RULES_MESSAGE,
            )
        )
        creator = ParticipantRef(user_type="user", user_id=user_id)
        await self._chats.log_event(thread.id, creator, "created")
        await self._uow.commit()
        logger.info(
            "group_thread_created thread=%s user_id=%s listing_id=%s",
            thread.id,
            user_id,
            listing_id,
        )
        return thread.id


class CreateInvite:
    """POST /api/chats/{id}/invite — код приглашения (07 §2, 08 §2).

    Только **owner** переговорной (member → 403 `invite_owner_only`).
    Код — 6 символов base32 без путаницы (0/1/I/L/O/U исключены),
    redis `chat:invite:{code}` → thread_id, TTL 7 дней, многоразовый
    до лимита участников. Выпуск кода пишется в аудит (event='invited').
    """

    def __init__(
        self, chats: ChatRepository, cache: CachePort, uow: UnitOfWork
    ) -> None:
        self._chats = chats
        self._cache = cache
        self._uow = uow

    async def execute(self, thread_id: UUID, requester: ParticipantRef) -> str:
        """Выпустить код; повторный вызов выдаёт новый код.

        Raises:
            NotFound: Тред недоступен субъекту.
            Conflict: Тред не group — инвайты только для переговорных.
            Forbidden: Инициатор не владелец (invite_owner_only, 08 §2).
        """
        thread = await self._chats.get_participant_thread(
            thread_id, requester.user_type, requester.user_id
        )
        if thread is None:
            raise NotFound(CHAT_NOT_FOUND)
        if thread.type != "group":
            raise Conflict(NOT_A_GROUP_THREAD)
        # §2: приглашает только owner — member'у отлуп.
        if await _actor_role(self._chats, thread, requester) != "owner":
            raise Forbidden(INVITE_OWNER_ONLY)
        for _ in range(_INVITE_CODE_ATTEMPTS):
            code = "".join(
                secrets.choice(INVITE_ALPHABET)
                for _ in range(INVITE_CODE_LEN)
            )
            # SET NX: коллизия с живым кодом не перезаписывает чужой тред.
            if await self._cache.set_if_absent(
                _invite_redis_key(code), str(thread_id), INVITE_TTL_SEC
            ):
                await self._chats.log_event(
                    thread_id, requester, "invited"
                )
                await self._uow.commit()
                logger.info(
                    "group_invite_issued thread=%s by=%s:%s",
                    thread_id,
                    requester.user_type,
                    requester.user_id,
                )
                return code
        raise Conflict("invite_conflict: не удалось выдать код, повторите")


class JoinByCode:
    """POST /api/chats/join — войти в переговорную по коду (07 §2).

    404 — код истёк/не существует; 409 group_is_full — участников уже 5.
    Уже участник (в т.ч. создатель) → тот же тред без добавления (200).
    Лимит считается атомарно: advisory-lock по хэшу thread_id закрывает
    гонку двух параллельных join на последнее место (07 §5).
    """

    def __init__(
        self, chats: ChatRepository, cache: CachePort, uow: UnitOfWork
    ) -> None:
        self._chats = chats
        self._cache = cache
        self._uow = uow

    async def execute(
        self, code: str, requester: ParticipantRef
    ) -> tuple[UUID, bool]:
        """Присоединиться; `(thread_id, joined)`.

        Raises:
            NotFound: Код истёк/не существует или тред не group.
            Conflict: В переговорной уже 5 участников.
        """
        raw = await self._cache.get(_invite_redis_key(code.strip().upper()))
        if raw is None:
            raise NotFound(INVITE_NOT_FOUND)
        try:
            thread_id = UUID(str(raw))
        except ValueError as exc:
            raise NotFound(INVITE_NOT_FOUND) from exc
        # Уже участник (создатель/ранее вошедший) — идемпотентный 200.
        thread = await self._chats.get_participant_thread(
            thread_id, requester.user_type, requester.user_id
        )
        if thread is not None:
            if thread.type != "group":
                raise NotFound(CHAT_NOT_FOUND)
            return thread.id, False
        # Serialization point: лок держит счёт участников стабильным,
        # пока проверяем <5 и вставляем строку (та же транзакция).
        await self._chats.lock_thread(thread_id)
        # Повторная проверка участия под локом: параллельный join того же
        # субъекта (двойной клик) после коммита первого — идемпотентный
        # 200, а не 409 group_is_full по собственному место-занятию.
        thread = await self._chats.get_participant_thread(
            thread_id, requester.user_type, requester.user_id
        )
        if thread is not None:
            return thread.id, False
        thread = await self._chats.get_thread(thread_id)
        if thread is None or thread.type != "group":
            raise NotFound(CHAT_NOT_FOUND)
        if (
            await self._chats.count_participants(thread_id)
            >= GROUP_MAX_PARTICIPANTS
        ):
            raise Conflict(GROUP_IS_FULL)
        await self._chats.ensure_participant(
            thread_id, requester.user_type, requester.user_id
        )
        # §5: аудит входа (role='member' по умолчанию репозитория).
        await self._chats.log_event(thread_id, requester, "joined")
        await self._uow.commit()
        logger.info(
            "group_joined thread=%s by=%s:%s",
            thread_id,
            requester.user_type,
            requester.user_id,
        )
        return thread_id, True


class CreateSupportThread:
    """POST /api/chats/support — singleton поддержки на юзера (reuse)."""

    def __init__(self, chats: ChatRepository, uow: UnitOfWork) -> None:
        self._chats = chats
        self._uow = uow

    async def execute(self, user_id: int) -> tuple[UUID, bool]:
        """Создать/вернуть тред поддержки; `(thread_id, created)`.

        При создании пишется системное приветствие (§6).
        """
        thread, created = await self._chats.get_or_create_thread(
            type="support", user_id=user_id
        )
        if created:
            await self._chats.add_message(
                SendMessageCommand(
                    thread_id=thread.id,
                    sender=_system_sender(),
                    body=SUPPORT_GREETING,
                )
            )
            logger.info("support_thread_created user_id=%s", user_id)
        await self._uow.commit()
        return thread.id, created


class CreateSellerThread:
    """POST /api/chats/seller/{listing_id} — чат с продавцом лота (reuse).

    Продавец = владелец owner-лота рынка (06 §5): юзер (тред двух
    пользователей, владелец — вторая строка chat_participants) или партнёр
    (partner_id треда). Внешние лоты (owner_type IS NULL) — 409.
    """

    def __init__(
        self,
        chats: ChatRepository,
        listings: ListingReadRepository,
        lots: MarketLotRepository,
        uow: UnitOfWork,
    ) -> None:
        self._chats = chats
        self._listings = listings
        self._lots = lots
        self._uow = uow

    async def execute(
        self, user_id: int, listing_id: int
    ) -> tuple[UUID, bool]:
        """Создать/вернуть seller-тред; `(thread_id, created)`.

        Raises:
            NotFound: Лота нет или он неактивен.
            Conflict: Внешний лот без владельца (06 §5) — чата нет.
        """
        if await self._listings.get_active(listing_id) is None:
            raise NotFound("listing_not_found: лот недоступен")
        lot = await self._lots.get_lot(listing_id)
        if lot is None or lot.owner_type is None:
            raise Conflict(MARKET_LOT_REQUIRED)

        if lot.owner_type == "user":
            # Тред двух пользователей: вторая строка участника — владелец
            # лота (его список чатов ищет треды по участию, §2).
            thread, created = await self._chats.get_or_create_thread(
                type="seller",
                user_id=user_id,
                partner_id=None,
                listing_id=listing_id,
            )
            owner_user_id = lot.owner_user_id
            if owner_user_id is not None and owner_user_id != user_id:
                await self._chats.ensure_participant(
                    thread.id, "user", owner_user_id
                )
        else:
            thread, created = await self._chats.get_or_create_thread(
                type="seller",
                user_id=user_id,
                partner_id=lot.owner_partner_id,
                listing_id=listing_id,
            )
        if created:
            await self._chats.add_message(
                SendMessageCommand(
                    thread_id=thread.id,
                    sender=_system_sender(),
                    body=SELLER_GREETING,
                )
            )
            logger.info(
                "seller_thread_created user_id=%s listing_id=%s owner_type=%s",
                user_id,
                listing_id,
                lot.owner_type,
            )
        await self._uow.commit()
        return thread.id, created


class ListMessages:
    """GET /api/chats/{id}/messages — пагинация вниз + after_id-поллинг."""

    def __init__(self, chats: ChatRepository) -> None:
        self._chats = chats

    async def execute(
        self,
        thread_id: UUID,
        requester: ParticipantRef,
        *,
        before_id: int | None = None,
        after_id: int | None = None,
        limit: int = 50,
    ) -> list[MessageView]:
        """Сообщения треда по возрастанию id (проверка участия).

        Raises:
            NotFound: Тред недоступен субъекту.
        """
        if (
            await self._chats.get_participant_thread(
                thread_id, requester.user_type, requester.user_id
            )
            is None
        ):
            raise NotFound("chat_not_found: тред не найден")
        return await self._chats.messages(
            thread_id, before_id=before_id, after_id=after_id, limit=limit
        )


class SendMessage:
    """POST /{id}/messages — markdown <=4000; публикация в канал треда.

    Приватность (08 §3): в тредах seller/deal/group контакты (телефоны,
    внешние мессенджеры) маскируются ДО валидации длины — длина
    проверяется по итоговому тексту (маска длиннее оригинала).
    """

    # Типы тредов с маскировкой (08 §3); support — оператор = площадка.
    MASKED_THREAD_TYPES = ("seller", "deal", "group")

    def __init__(
        self,
        chats: ChatRepository,
        publisher: ThreadEventPublisher,
        uow: UnitOfWork,
        bot_username: str = "",
    ) -> None:
        self._chats = chats
        self._publisher = publisher
        self._uow = uow
        # Наш бот (t.me/<он> не маскируется); пусто — маскируем все t.me.
        self._bot_username = bot_username

    async def execute(
        self, thread_id: UUID, sender: ParticipantRef, body: str
    ) -> MessageView:
        """Записать сообщение и опубликовать событие message (§4).

        Raises:
            NotFound: Тред недоступен автору.
            Forbidden: Тред закрыт (closed → read-only, §2).
            InvalidInput: Пустое тело или длиннее 4000 символов
                (после маскировки).
        """
        thread = await self._chats.get_participant_thread(
            thread_id, sender.user_type, sender.user_id
        )
        if thread is None:
            raise NotFound("chat_not_found: тред не найден")
        if thread.status != "active":
            raise Forbidden(THREAD_ARCHIVED)
        # §3: маскировка до валидации — лимит считаем по итоговому тексту.
        if thread.type in self.MASKED_THREAD_TYPES:
            body = mask_contacts(body, self._bot_username)
        body = _validated_body(body)

        message = await self._chats.add_message(
            SendMessageCommand(thread_id=thread_id, sender=sender, body=body)
        )
        await self._uow.commit()
        # 09 §1: событие с полным MessageView уходит в личные каналы
        # участников (отдельное уведомление user:{id} не нужно — это и
        # есть личный канал).
        await self._publisher.publish_to_participants(
            thread_id, message_event(thread_id, message)
        )
        return message


class SummonOperator:
    """POST /api/chats/{id}/summon-operator — юзер позвал человека (§6).

    Только support-тред иницииатора: `bot_paused=True` + system-сообщение
    «Пользователь позвал оператора — бот на паузе» + публикация в канал
    треда. Идемпотентно: повторный зов при уже стоящей паузе ничего не
    меняет (без второй system-строки) — клиент получает тот же 200.
    """

    def __init__(
        self,
        chats: ChatRepository,
        publisher: ThreadEventPublisher,
        uow: UnitOfWork,
    ) -> None:
        self._chats = chats
        self._publisher = publisher
        self._uow = uow

    async def execute(
        self, thread_id: UUID, user_id: int
    ) -> tuple[ThreadRecord, MessageView | None]:
        """Поставить бота на паузу и записать system-зов оператора.

        Returns:
            `(тред, system-сообщение)`; при уже стоящей паузе —
            `(тред, None)` без записи и публикации.

        Raises:
            NotFound: Тред не принадлежит юзеру.
            Conflict: Тред не support — оператор зовётся только в поддержке.
            Forbidden: Тред закрыт.
        """
        thread = await self._chats.get_participant_thread(
            thread_id, "user", user_id
        )
        if thread is None:
            raise NotFound("chat_not_found: тред не найден")
        if thread.type != "support":
            raise Conflict(NOT_A_SUPPORT_THREAD)
        if thread.status != "active":
            raise Forbidden("chat_closed: тред закрыт, поддержка недоступна")
        if thread.bot_paused:
            # Уже позвали / оператор уже писал — повтор ничего не меняет.
            return thread, None

        await self._chats.pause_bot(thread_id)
        message = await self._chats.add_message(
            SendMessageCommand(
                thread_id=thread_id,
                sender=_system_sender(),
                body=SUMMON_OPERATOR_MESSAGE,
            )
        )
        await self._uow.commit()
        await self._publisher.publish_to_participants(
            thread_id, message_event(thread_id, message)
        )
        logger.info("operator_summoned thread=%s user_id=%s", thread_id, user_id)
        return thread, message


class ListOperatorThreads:
    """GET /api/admin/chats — список тредов для оператора (§2/§6)."""

    def __init__(self, chats: ChatRepository) -> None:
        self._chats = chats

    async def execute(
        self,
        thread_type: str = "support",
        *,
        paused: bool | None = None,
        limit: int = 50,
    ) -> list[OperatorThreadView]:
        """Треды типа: юзер, последнее юзер-сообщение, `bot_paused`."""
        return await self._chats.list_threads_for_operator(
            thread_type, paused=paused, limit=limit
        )


class CountOperatorUnread:
    """GET /api/admin/chats/unread — бейдж «ждут ответа» для поллинга."""

    def __init__(self, chats: ChatRepository) -> None:
        self._chats = chats

    async def execute(self, thread_type: str = "support") -> int:
        """Сколько тредов с юзер-сообщением позже последнего ответа оператора."""
        return await self._chats.count_awaiting_operator(thread_type)


class ListOperatorMessages:
    """GET /api/admin/chats/{id}/messages — оператор видит всё (§2)."""

    def __init__(self, chats: ChatRepository) -> None:
        self._chats = chats

    async def execute(
        self,
        thread_id: UUID,
        *,
        before_id: int | None = None,
        after_id: int | None = None,
        limit: int = 50,
    ) -> list[MessageView]:
        """Сообщения треда по возрастанию id (без проверки участия).

        Raises:
            NotFound: Треда нет.
        """
        if await self._chats.get_thread(thread_id) is None:
            raise NotFound("chat_not_found: тред не найден")
        return await self._chats.messages(
            thread_id, before_id=before_id, after_id=after_id, limit=limit
        )


class SendOperatorMessage:
    """POST /api/admin/chats/{id}/messages — оператор отвечает (§2/§6).

    Репозиторий сам ставит `bot_paused=True` (оператор писал последним —
    бот молчит, пока юзер снова не напишет) и снимает при сообщении юзера.
    """

    def __init__(
        self,
        chats: ChatRepository,
        publisher: ThreadEventPublisher,
        uow: UnitOfWork,
    ) -> None:
        self._chats = chats
        self._publisher = publisher
        self._uow = uow

    async def execute(
        self, thread_id: UUID, body: str, sender: ParticipantRef | None = None
    ) -> MessageView:
        """Записать ответ оператора и опубликовать события (§4).

        Raises:
            NotFound: Треда нет.
            Forbidden: Тред закрыт (closed → read-only, §2).
            InvalidInput: Пустое тело или длиннее 4000 символов.
        """
        operator = sender or _operator_sender()
        thread = await self._chats.get_thread(thread_id)
        if thread is None:
            raise NotFound("chat_not_found: тред не найден")
        if thread.status != "active":
            raise Forbidden("chat_closed: тред закрыт, отправка недоступна")
        body = _validated_body(body)

        message = await self._chats.add_message(
            SendMessageCommand(thread_id=thread_id, sender=operator, body=body)
        )
        await self._uow.commit()
        # 09 §1: личные каналы участников (юзер получает полный MessageView).
        await self._publisher.publish_to_participants(
            thread_id, message_event(thread_id, message)
        )
        return message


class MarkRead:
    """POST /{id}/read — поднять read-маркер участника (§5)."""

    def __init__(
        self,
        chats: ChatRepository,
        publisher: ThreadEventPublisher,
        uow: UnitOfWork,
    ) -> None:
        self._chats = chats
        self._publisher = publisher
        self._uow = uow

    async def execute(
        self,
        thread_id: UUID,
        reader: ParticipantRef,
        last_read_message_id: int | None = None,
    ) -> int:
        """Отметить прочтение (без id — до последнего); вернуть маркер.

        Raises:
            NotFound: Тред недоступен читателю.
        """
        if (
            await self._chats.get_participant_thread(
                thread_id, reader.user_type, reader.user_id
            )
            is None
        ):
            raise NotFound("chat_not_found: тред не найден")
        if last_read_message_id is None:
            last_read_message_id = await self._chats.max_message_id(thread_id)
        await self._chats.mark_read(
            MarkReadCommand(
                thread_id=thread_id,
                reader=reader,
                last_read_message_id=last_read_message_id,
            )
        )
        await self._uow.commit()
        await self._publisher.publish_to_participants(
            thread_id,
            {
                "type": "read",
                "thread_id": str(thread_id),
                "user_type": reader.user_type,
                "user_id": reader.user_id,
                "last_read_message_id": last_read_message_id,
            },
        )
        return last_read_message_id


class SendTyping:
    """POST /{id}/typing — «печатает» другим участникам (09 §1).

    Эфемерное событие `{type:'typing', thread_id, user_type}` уходит в
    личные каналы ДРУГИХ участников (свои echo не нужны — отправитель и
    так знает, что печатает). Не пишется в БД; rate-limit 1/2с держит
    транспорт (как у message, 08 §4).
    """

    def __init__(
        self,
        chats: ChatRepository,
        publisher: ThreadEventPublisher,
    ) -> None:
        self._chats = chats
        self._publisher = publisher

    async def execute(self, thread_id: UUID, sender: ParticipantRef) -> None:
        """Опубликовать typing от отправителя (кроме него самого).

        Raises:
            NotFound: Тред недоступен отправителю (не участник).
        """
        if (
            await self._chats.get_participant_thread(
                thread_id, sender.user_type, sender.user_id
            )
            is None
        ):
            raise NotFound(CHAT_NOT_FOUND)
        await self._publisher.publish_to_participants(
            thread_id,
            {"type": "typing", "user_type": sender.user_type},
            exclude=sender,
        )


# Лимит ids онлайн-запроса: один запрос — одна комната (участников <=5),
# 50 с запасом на сайдбар/списки (09 §1).
ONLINE_IDS_MAX = 50


class GetOnlineUsers:
    """GET /api/chats/online?ids= — presence личных каналов (09 §1).

    Онлайн = в канале `user:{telegram_id}` есть хоть один клиент
    (HTTP API centrifugo `presence`). Ошибка/пусто канала — офлайн;
    centrifugo не настроен (`enabled=False`) — честный пустой список.
    """

    def __init__(self, presence: ChannelPresencePort) -> None:
        self._presence = presence

    async def execute(self, ids: Sequence[int]) -> list[int]:
        """Telegram_id онлайн-юзеров среди запрошенных (порядок сохраняем).

        Дубликаты ids схлопываются; каналы опрашиваются параллельно —
        запрос комнаты не должен сериализовать 50 round-trip-ов.
        """
        if not self._presence.enabled:
            return []
        unique = list(dict.fromkeys(ids))
        snapshots = await asyncio.gather(
            *(self._presence.presence(f"user:{tg}") for tg in unique)
        )
        return [
            tg
            for tg, clients in zip(unique, snapshots, strict=True)
            if clients
        ]


class IssueWsToken:
    """GET /api/chats/ws-token — браузерный JWT centrifugo (§4).

    `sub` = `user:{telegram_id}` (мини-апп) или `partner:{id}` (кабинет);
    HS256, exp=+12ч, подпись — CENTRIFUGO_SECRET (в инфраструктуре).
    """

    def __init__(self, signer: WsTokenFactory, url: str) -> None:
        self._signer = signer
        self._url = url

    async def execute(self, sub: str) -> WsTokenView:
        """Выпустить токен для субъекта (без обращений к БД)."""
        token = self._signer.issue(sub, WS_TOKEN_TTL_SEC)
        return WsTokenView(token=token, url=self._url)


class HandleSubscribeProxy:
    """POST /api/chats/subscribe-proxy — валидация подписки (§4, §2).

    Centrifugo присылает `{client, channel, user, ...}`: `user` — sub
    connection-JWT (`user:{id}` | `partner:{id}`). Личный канал `user:{X}`
    разрешён только самому X; `thread:{uuid}` — только участникам.
    """

    def __init__(self, chats: ChatRepository) -> None:
        self._chats = chats

    async def execute(self, channel: str, sub: str | None) -> bool:
        """Разрешена ли подписка; `False` → centrifugo получает error 403."""
        if not channel or not sub:
            return False
        if channel.startswith("user:"):
            # Личный канал — строго свой.
            return sub == channel
        if not channel.startswith("thread:"):
            return False
        try:
            thread_id = UUID(channel.removeprefix("thread:"))
        except ValueError:
            return False
        if sub.startswith("user:"):
            user_type, raw_id = "user", sub.removeprefix("user:")
        elif sub.startswith("partner:"):
            user_type, raw_id = "partner", sub.removeprefix("partner:")
        else:
            return False
        if not raw_id.isdigit():
            return False
        return await self._chats.is_participant(thread_id, user_type, int(raw_id))


def message_event(thread_id: UUID, message: MessageView) -> dict[str, Any]:
    """Payload события message: общий формат для сокета и поллинга (§4)."""
    return {
        "type": "message",
        "thread_id": str(thread_id),
        "id": message.id,
        "sender_type": message.sender_type,
        "sender_id": message.sender_id,
        "body": message.body,
        "created_at": message.created_at.isoformat(),
    }
