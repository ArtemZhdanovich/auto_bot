"""Порты чатов: репозиторий, публикация в centrifugo, загрузка превью."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from app.application.chat.dto import (
    AdminReportView,
    ChatEventView,
    MarkReadCommand,
    MessageView,
    OperatorThreadView,
    ParticipantRef,
    ParticipantView,
    SendMessageCommand,
    ThreadRecord,
    ThreadView,
)
from app.application.errors import InvalidInput


class ChatRepository(Protocol):
    """Хранилище тредов/сообщений/участников (реализация — SQLAlchemy)."""

    async def list_threads_for_user(self, user_id: int) -> list[ThreadView]:
        """Треды юзера (владелец или участник) с unread и последним сообщением."""

    async def list_threads_for_partner(
        self, partner_id: int
    ) -> list[ThreadView]:
        """Seller/deal-треды партнёрского счёта (unread по участнику-партнёру)."""

    async def get_participant_thread(
        self, thread_id: UUID, user_type: str, user_id: int
    ) -> ThreadRecord | None:
        """Тред, доступный субъекту (владение или строка участника), иначе None."""

    async def get_thread(self, thread_id: UUID) -> ThreadRecord | None:
        """Тред по id без проверки доступа (оператор видит все, §2)."""

    async def pause_bot(self, thread_id: UUID) -> None:
        """Поставить бот на паузу (зов оператора, §6).

        Обратный сбой делает `add_message` при сообщении юзера;
        оператор держит паузу через sender_type='operator'.
        """

    async def list_threads_for_operator(
        self,
        thread_type: str = "support",
        *,
        paused: bool | None = None,
        limit: int = 50,
    ) -> list[OperatorThreadView]:
        """Треды типа для оператора: имя юзера, последнее юзер-сообщение
        (время + сниппет) и `bot_paused`; сортировка по свежести."""

    async def count_awaiting_operator(self, thread_type: str = "support") -> int:
        """Сколько тредов ждут ответа оператора: есть юзер-сообщение позже
        последнего сообщения оператора (или операторов ещё не было, §2/§6)."""

    async def is_participant(
        self, thread_id: UUID, user_type: str, user_id: int
    ) -> bool:
        """Быстрая проверка участия (для subscribe-proxy)."""

    async def get_or_create_thread(
        self,
        *,
        type: str,
        user_id: int,
        partner_id: int | None = None,
        listing_id: int | None = None,
        lead_id: int | None = None,
    ) -> tuple[ThreadRecord, bool]:
        """Найти тред по срезу типа (частичный unique) или создать.

        Возвращает `(тред, created)`; гонки двух запросов разрешает
        ON CONFLICT по частичному индексу — оба получают один тред.
        """

    async def ensure_participant(
        self, thread_id: UUID, user_type: str, user_id: int, *,
        role: str = "member",
    ) -> None:
        """Создать строку участника, если её нет (идемпотентно).

        Владелец-юзер seller-треда (06 §5): вторая сторона переписки.
        `role` (08 §2): 'owner' для создателя group-треда, 'member' для
        вошедших по коду; на конфликте роль существующей строки не трогаем.
        """

    async def participant_role(
        self, thread_id: UUID, user_type: str, user_id: int
    ) -> str | None:
        """Роль субъекта в треде ('owner'|'member') или None без строки.

        Говернанс-проверки 08 §2: invite — только owner, kick — owner
        кикает member, leave owner'а — только из пустой переговорной.
        """

    async def remove_participant(
        self, thread_id: UUID, user_type: str, user_id: int
    ) -> bool:
        """Удалить строку участника (kick/leave, 08 §2).

        Возвращает False, если строки не было (не участник).
        """

    async def set_muted(
        self,
        thread_id: UUID,
        user_type: str,
        user_id: int,
        muted: bool,
        *,
        role: str = "member",
    ) -> None:
        """Поставить/снять свой mute (08 §6): upsert строки участника.

        Строки может не быть (owner-юзер без read-маркера) — создаём с
        ролью `role` (владельцу — 'owner', чтобы не потерять роль в
        выдаче ThreadOut).
        """

    async def archive_thread(self, thread_id: UUID) -> None:
        """Закрыть тред: status='closed' + closed_at=now() (08 §2/§5)."""

    async def log_event(
        self,
        thread_id: UUID,
        actor: ParticipantRef,
        event: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        """Записать аудит-событие треда (08 §5): actor — инициатор.

        События: created/joined/left/invited/kicked/archived/reported/
        muted/unmuted; пишется во всех операциях говернанса §2-§4.
        """

    async def list_events(
        self, thread_id: UUID, *, limit: int = 100
    ) -> list[ChatEventView]:
        """Лента событий треда, новые сверху (id DESC, 08 §5)."""

    async def create_report(
        self,
        thread_id: UUID,
        reporter: ParticipantRef,
        reason: str,
        comment: str | None,
    ) -> int | None:
        """Создать жалобу (08 §4); None — «открытая» уже есть (дубль).

        Идемпотентность — частичный unique (thread, reporter, reason)
        WHERE status='new': ON CONFLICT DO NOTHING.
        """

    async def list_reports(
        self, status: str = "new", *, limit: int = 100
    ) -> list[AdminReportView]:
        """Жалобы по статусу: + имя репортёра и сниппет треда (08 §4)."""

    async def handle_report(self, report_id: int, status: str) -> bool:
        """Отметить жалобу обработанной (resolved|dismissed) + handled_at.

        Возвращает False, если жалобы с таким id нет.
        """

    async def create_group_thread(
        self, *, user_id: int, title: str, listing_id: int | None = None
    ) -> ThreadRecord:
        """Создать group-тред (07 §2) и вернуть запись.

        Естественного ключа нет (сколько переговорных — столько тредов),
        advisory-lock при создании не нужен: создатель один, участники
        добавляются только через invite/join.
        """

    async def count_participants(self, thread_id: UUID) -> int:
        """Сколько строк участников у треда (лимит group <=5, 07 §2)."""

    async def lock_thread(self, thread_id: UUID) -> None:
        """Advisory-lock треда (pg_advisory_xact_lock по хэшу id, 07 §5).

        Сериализует проверку «участников < 5» с INSERT'ом участника в
        JoinByCode: без лока два параллельных join видят 4 и проходят оба.
        """

    async def list_participants(
        self, thread_id: UUID
    ) -> list["ParticipantView"]:
        """Участники group-треда с именами (users / partner_accounts, 07 §2)."""

    async def messages(
        self,
        thread_id: UUID,
        *,
        before_id: int | None = None,
        after_id: int | None = None,
        limit: int = 50,
    ) -> list[MessageView]:
        """Сообщения по возрастанию id.

        `before_id` — пагинация вниз (id < before_id), `after_id` —
        поллинг-инкремент (id > after_id); взаимоисключающие.
        """

    async def add_message(self, command: SendMessageCommand) -> MessageView:
        """Записать сообщение, обновить `last_message_at` (и bot_paused §6)."""

    async def count_messages(
        self, thread_id: UUID, *, sender_type: str, since: datetime
    ) -> int:
        """Сколько сообщений отправителя в треде с момента `since`.

        Окно суточного лимита бота поддержки (§6): считаем user-сообщения
        треда за последние 24 часа.
        """

    async def attach_partner_to_deal(
        self, lead_id: int, partner_id: int
    ) -> ThreadRecord | None:
        """Привязать партнёра к deal-треду лида (claim, §1).

        Возвращает тред (с уже проставленным `partner_id`) или `None`,
        если deal-треда у лида нет (например, лид из пула «свяжитесь со
        мной» без заявки на агента).
        """

    async def max_message_id(self, thread_id: UUID) -> int:
        """Последний id сообщений треда (маркер «прочитано всё»)."""

    async def mark_read(self, command: MarkReadCommand) -> None:
        """Поднять read-маркер участника (монотонно, не опускается)."""


class ChatPublisher(Protocol):
    """Публикация событий в каналы centrifugo (§4: message|read|typing).

    Реализация fire-and-forget: сбой centrifugo не ломает REST-запрос —
    клиент доедет поллингом (`after_id`).
    """

    async def publish(self, channel: str, data: dict[str, Any]) -> None: ...


class ThreadEventPublisher(Protocol):
    """Единая точка публикации событий треда (09 §1): личные каналы.

    Клиент подписан только на свой канал `user:{telegram_id}` /
    `partner:{id}` — события треда (message/read/typing/system)
    рассылаются в личные каналы ВСЕХ участников. Если в payload нет
    `thread_id` — реализация добавляет его сама.
    """

    async def publish_to_participants(
        self,
        thread_id: UUID,
        event: dict[str, Any],
        *,
        exclude: ParticipantRef | None = None,
    ) -> None:
        """Разослать событие участникам; `exclude` — кому не слать (typing)."""


class ChannelPresencePort(Protocol):
    """Онлайн-статусы: presence личных каналов centrifugo (09 §1).

    `enabled=False` — centrifugo не настроен (dev-заглушки ключей):
    онлайн-эндпоинты честно отвечают пустым списком, поллинг статусов
    не должен изображать «все офлайн» через сотню падающих запросов.
    """

    @property
    def enabled(self) -> bool: ...

    async def presence(self, channel: str) -> set[str]:
        """Client-id в канале; пусто/ошибка — субъект офлайн."""


class LinkBlockedError(InvalidInput):
    """Ссылка запрещена SSRF-фильтром (приватный IP/схема) — клиенту 400."""

    code = "invalid_input"
    default_message = "link_forbidden: ссылка на приватный ресурс запрещена"


class LinkFetcherPort(Protocol):
    """Загрузка HTML внешней страницы (первый ~512KB) с SSRF-защитой.

    Возвращает HTML или None (страница недоступна); запрещённые адреса
    бросают LinkBlockedError. Редиректы (максимум один) проверяются тем же
    фильтром.
    """

    async def fetch(self, url: str) -> str | None: ...


class WsTokenFactory(Protocol):
    """Подпись браузерного JWT centrifugo (HS256, секрет — CENTRIFUGO_SECRET).

    Живёт в инфраструктуре рядом с publish-клиентом: один адаптер
    `CentrifugoPublisher` закрывает оба порта.
    """

    def issue(self, sub: str, ttl_sec: int) -> str: ...


class MarketLike(Protocol):
    """Витрина для сценария чата: продавец лота (подмножество MarketRepository)."""

    async def partner_id_for_listing(self, listing_id: int) -> int | None:
        """Партнёр-продавец лота или None."""
