"""DTO чатов: треды, сообщения, участники и команды (редизайн часть 5 §3, §5)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

# Максимальная длина сообщения (markdown, §3): Pydantic-схема + сценарий.
MAX_MESSAGE_LEN = 4000

# Личный канал уведомлений юзера и партнёра (§4: user:{telegram_id},
# partner:{id} для кабинета) — префиксы sub в ws-token.
USER_SUB_PREFIX = "user:"
PARTNER_SUB_PREFIX = "partner:"

# TTL браузерного JWT centrifugo (§4: exp=+12ч).
WS_TOKEN_TTL_SEC = 12 * 60 * 60


@dataclass(frozen=True, slots=True)
class ThreadRecord:
    """Строка `chat_threads` для прикладного слоя (без ORM)."""

    id: UUID
    type: str
    user_id: int
    partner_id: int | None = None
    listing_id: int | None = None
    lead_id: int | None = None
    status: str = "active"
    bot_paused: bool = False
    # Название переговорной (group, 07 §2); у service-типов — None.
    title: str | None = None
    # Момент закрытия (08 §2/§5): архив или пустой тред после leave.
    closed_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ParticipantView:
    """Участник group-треда в списке чатов (07 §2): субъект + имя.

    Имя: юзер — first_name/username; партнёр — название счёта.
    Роль (08 §2): 'owner' — корона владельца, 'member' — обычный.
    """

    user_type: str
    user_id: int
    name: str | None = None
    role: str = "member"


@dataclass(frozen=True, slots=True)
class ParticipantRef:
    """Субъект доступа к треду: user → telegram_id, partner → id счёта."""

    user_type: str
    user_id: int


@dataclass(frozen=True, slots=True)
class SubjectCard:
    """Мини-карточка объявления — предмет треда (seller/deal)."""

    id: int
    make: str | None
    model: str | None
    year: int | None
    price: float | None
    currency: str | None
    url: str | None
    image: str | None = None


@dataclass(frozen=True, slots=True)
class LastMessageView:
    """Последнее сообщение треда в списке (плоский текст)."""

    body_plain: str
    created_at: datetime
    sender_type: str
    sender_id: int | None


@dataclass(frozen=True, slots=True)
class ThreadView:
    """Тред в списке чатов (GET /api/chats, GET /api/partner/chats)."""

    id: UUID
    type: str
    title: str
    status: str
    unread: int = 0
    last_message: LastMessageView | None = None
    subject: SubjectCard | None = None
    # Deal-тред: лид заявки и его партнёрский статус (claimed/accepted/
    # returned/junk/deal) — партнёрский кабинет по нему решает, показывать
    # ли CTA «Отметить сделкой» (долг ревизора №6). None — не deal-тред.
    lead_id: int | None = None
    lead_status: str | None = None
    # Переговорная (group, 07 §2): флаг + участники с именами для шапки
    # комнаты; у остальных типов participants пуст.
    is_group: bool = False
    participants: tuple[ParticipantView, ...] = ()
    # Говернанс (08 §2/§6): своя роль в треде ('owner'|'member') и свой
    # mute (muted-тред не дёргает бейдж конверта — фильтрует фронт).
    role: str = "member"
    muted: bool = False


@dataclass(frozen=True, slots=True)
class OperatorThreadView:
    """Тред в списке оператора поддержки (GET /api/admin/chats, §2/§6).

    Unread по участнику-оператору не считается: строки участника-оператора
    в треде может не быть. Вместо неё — время и сниппет последнего
    юзер-сообщения + `bot_paused` (юзер позвал оператора / оператор
    вклинился) — оператору этого достаточно, чтобы выбрать тред.
    """

    id: UUID
    type: str
    status: str
    bot_paused: bool
    user_id: int
    user_name: str | None = None
    last_user_message_at: datetime | None = None
    last_user_message_snippet: str | None = None


@dataclass(frozen=True, slots=True)
class MessageView:
    """Сообщение чата (GET/POST /{id}/messages)."""

    id: int
    sender_type: str
    sender_id: int | None
    body: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class SendMessageCommand:
    """Команда отправки сообщения: тред + автор + markdown-тело."""

    thread_id: UUID
    sender: ParticipantRef
    body: str


@dataclass(frozen=True, slots=True)
class MarkReadCommand:
    """Команда отметки прочтения; `None` = прочитать всё (до последнего)."""

    thread_id: UUID
    reader: ParticipantRef
    last_read_message_id: int | None


@dataclass(frozen=True, slots=True)
class WsTokenView:
    """Браузерный JWT centrifugo + адрес сервера (GET /ws-token, §4)."""

    token: str
    url: str


@dataclass(frozen=True, slots=True)
class AdminReportView:
    """Жалоба в списке админа (08 §4): репорт + контекст треда.

    `thread_snippet` — плоский текст последнего сообщения треда (с чем
    работает модерация), `reporter_name` — отображаемое имя репортёра.
    """

    id: int
    thread_id: UUID
    reporter_type: str
    reporter_id: int
    reporter_name: str | None
    reason: str
    comment: str | None
    status: str
    created_at: datetime
    handled_at: datetime | None = None
    thread_title: str | None = None
    thread_snippet: str | None = None


@dataclass(frozen=True, slots=True)
class ChatEventView:
    """Строка ленты событий треда (08 §5): новые сверху (id DESC)."""

    id: int
    thread_id: UUID
    actor_type: str
    actor_id: int | None
    event: str
    payload: dict[str, Any] | None
    created_at: datetime
