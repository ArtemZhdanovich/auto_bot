"""SQLAlchemy-реализация ChatRepository: треды, сообщения, участники (§3).

Уникальность треда по предмету — частичные unique-индексы, поэтому
создание идёт через INSERT ... ON CONFLICT DO NOTHING + повторный SELECT:
гонка двух параллельных запросов даёт обоим один и тот же тред.
Unread = COUNT(messages.id > last_read) на участнике (§3), свои сообщения
непрочитанными не считаются.

Говернанс (08 §2-§6): роли/mute берутся из строки участника тем же
подзапросом, что и read-маркер (одна точка корреляции с chat_threads);
аудит-события и жалобы — таблицы chat_events / chat_reports (0029);
retention — фильтр списка (closed старше 90 дней скрыт, cron не нужен).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import (
    and_,
    delete,
    func,
    literal,
    or_,
    select,
    text as sa_text,
    update,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.chat.dto import (
    AdminReportView,
    ChatEventView,
    LastMessageView,
    MarkReadCommand,
    MessageView,
    OperatorThreadView,
    ParticipantRef,
    ParticipantView,
    SendMessageCommand,
    SubjectCard,
    ThreadRecord,
    ThreadView,
)
from app.application.chat.use_cases import plain_text
from app.infrastructure.db.models import (
    ChatEvent,
    ChatMessage,
    ChatParticipant,
    ChatReport,
    ChatThread,
    Lead,
    Listing,
    PartnerAccount,
    User,
)

# Уникальные срезы по типу треда: колонки + WHERE частичного индекса.
_UNIQUE_SLICES = {
    "support": (["type", "user_id"], "type = 'support'"),
    "seller": (
        ["type", "user_id", "listing_id"],
        "type = 'seller' AND listing_id IS NOT NULL",
    ),
    "deal": (["type", "lead_id"], "type = 'deal' AND lead_id IS NOT NULL"),
}

_TITLES = {
    "support": "Поддержка",
    "seller": "Продавец",
    "deal": "Сделка",
    # Group-треды без title (не бывает через API, fallback для legacy).
    "group": "Переговорная",
}

# Namespace advisory-lock'а group-тредов (не пересекается с 9106 market).
_GROUP_LOCK_NAMESPACE = 9107

# Retention (08 §5): закрытые треды старше 90 дней скрыты из списков чатов
# выборкой (cron не нужен — строки остаются в БД для юр. аудита).
CHAT_RETENTION_DAYS = 90


def _to_record(thread: ChatThread) -> ThreadRecord:
    """ORM-строка → ThreadRecord."""
    return ThreadRecord(
        id=thread.id,
        type=thread.type,
        user_id=thread.user_id,
        partner_id=thread.partner_id,
        listing_id=thread.listing_id,
        lead_id=thread.lead_id,
        status=thread.status,
        bot_paused=bool(thread.bot_paused),
        title=getattr(thread, "title", None),
        closed_at=getattr(thread, "closed_at", None),
    )


def _to_view(
    thread: ChatThread,
    subject: SubjectCard | None,
    unread: int,
    last: LastMessageView | None,
    lead_status: str | None = None,
    participants: tuple[ParticipantView, ...] = (),
    *,
    role: str = "member",
    muted: bool = False,
) -> ThreadView:
    """ORM-строка + собранные данные → ThreadView с заголовком."""
    is_group = thread.type == "group"
    if is_group:
        # Название переговорной задаёт юзер (07 §2); предмет лота — в шапке
        # комнаты отдельно, к title не приклеиваем.
        title = getattr(thread, "title", None) or _TITLES["group"]
    else:
        title = _TITLES.get(thread.type, thread.type)
        if subject is not None and subject.make:
            # «Продавец · Kia Carnival» — предмет читается без открытия чата.
            title = f"{title} · {subject.make} {subject.model or ''}".rstrip()
    return ThreadView(
        id=thread.id,
        type=thread.type,
        title=title,
        status=thread.status,
        unread=unread,
        last_message=last,
        subject=subject,
        # Статус лида только у deal-тредов (CTA «Отметить сделкой», №6).
        lead_id=thread.lead_id,
        lead_status=lead_status if thread.lead_id is not None else None,
        # Group-треды: флаг + участники с именами (07 §2).
        is_group=is_group,
        participants=participants if is_group else (),
        # Говернанс (08 §2/§6): своя роль и свой mute.
        role=role,
        muted=muted,
    )


class SqlAlchemyChatRepository:
    """Порт `ChatRepository` на `AsyncSession`."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # Треды
    # ------------------------------------------------------------------

    async def get_or_create_thread(
        self,
        *,
        type: str,
        user_id: int,
        partner_id: int | None = None,
        listing_id: int | None = None,
        lead_id: int | None = None,
    ) -> tuple[ThreadRecord, bool]:
        """INSERT по срезу типа (ON CONFLICT DO NOTHING) либо reuse."""
        columns, index_where = _UNIQUE_SLICES[type]
        stmt = (
            pg_insert(ChatThread)
            .values(
                type=type,
                user_id=user_id,
                partner_id=partner_id,
                listing_id=listing_id,
                lead_id=lead_id,
            )
            .on_conflict_do_nothing(
                index_elements=columns, index_where=sa_text(index_where)
            )
            .returning(ChatThread.id)
        )
        thread_id = (await self._session.execute(stmt)).scalar_one_or_none()
        if thread_id is not None:
            thread = await self._session.get(ChatThread, thread_id)
            assert thread is not None  # returning() в этой же транзакции
            return _to_record(thread), True
        return await self._select_existing(
            type, user_id=user_id, listing_id=listing_id, lead_id=lead_id
        )

    async def _select_existing(
        self,
        type: str,
        *,
        user_id: int,
        listing_id: int | None,
        lead_id: int | None,
    ) -> tuple[ThreadRecord, bool]:
        """Конфликт вставки → существующий тред по естественному ключу."""
        conditions = [ChatThread.type == type, ChatThread.user_id == user_id]
        if type == "seller":
            conditions.append(ChatThread.listing_id == listing_id)
        if type == "deal":
            conditions.append(ChatThread.lead_id == lead_id)
        thread = (
            (
                await self._session.execute(
                    select(ChatThread).where(*conditions)
                )
            )
            .scalars()
            .one_or_none()
        )
        assert thread is not None, "unique index guarantees the row"
        return _to_record(thread), False

    async def ensure_participant(
        self,
        thread_id: UUID,
        user_type: str,
        user_id: int,
        *,
        role: str = "member",
    ) -> None:
        """Создать строку участника, если её нет (ON CONFLICT DO NOTHING).

        Роль (08 §2): создатель group-треда — 'owner', вошедшие по коду —
        'member'; существующую строку не трогаем (роль не перезапишется).
        """
        stmt = (
            pg_insert(ChatParticipant)
            .values(
                thread_id=thread_id,
                user_type=user_type,
                user_id=user_id,
                role=role,
            )
            .on_conflict_do_nothing(
                index_elements=["thread_id", "user_type", "user_id"]
            )
        )
        await self._session.execute(stmt)

    async def create_group_thread(
        self, *, user_id: int, title: str, listing_id: int | None = None
    ) -> ThreadRecord:
        """Создать group-тред (07 §2): без естественного ключа/reuse."""
        thread = ChatThread(
            type="group",
            user_id=user_id,
            title=title,
            listing_id=listing_id,
        )
        self._session.add(thread)
        await self._session.flush()
        return _to_record(thread)

    async def count_participants(self, thread_id: UUID) -> int:
        """Сколько строк участников у треда (лимит group <=5)."""
        return int(
            (
                await self._session.execute(
                    select(func.count())
                    .select_from(ChatParticipant)
                    .where(ChatParticipant.thread_id == thread_id)
                )
            ).scalar_one()
        )

    async def lock_thread(self, thread_id: UUID) -> None:
        """Advisory-lock треда до конца транзакции (join-гонка лимита).

        `hashtext(uuid)` — стабильный int32 из id треда; namespace 9107
        отделяет от прочих advisory-локов приложения.
        """
        await self._session.execute(
            select(
                func.pg_advisory_xact_lock(
                    _GROUP_LOCK_NAMESPACE, func.hashtext(thread_id.hex)
                )
            )
        )

    # ------------------------------------------------------------------
    # Говернанс переговорных (08 §2-§6): роли, kick/leave, mute, архив
    # ------------------------------------------------------------------

    async def participant_role(
        self, thread_id: UUID, user_type: str, user_id: int
    ) -> str | None:
        """Роль субъекта ('owner'|'member') или None без строки участника."""
        return (
            await self._session.execute(
                select(ChatParticipant.role).where(
                    ChatParticipant.thread_id == thread_id,
                    ChatParticipant.user_type == user_type,
                    ChatParticipant.user_id == user_id,
                )
            )
        ).scalar_one_or_none()

    async def remove_participant(
        self, thread_id: UUID, user_type: str, user_id: int
    ) -> bool:
        """Удалить строку участника (kick/leave); False — строки не было."""
        result = await self._session.execute(
            delete(ChatParticipant).where(
                ChatParticipant.thread_id == thread_id,
                ChatParticipant.user_type == user_type,
                ChatParticipant.user_id == user_id,
            )
        )
        return bool(result.rowcount)

    async def set_muted(
        self,
        thread_id: UUID,
        user_type: str,
        user_id: int,
        muted: bool,
        *,
        role: str = "member",
    ) -> None:
        """Upsert своего mute (08 §6): роли существующей строки не меняем."""
        stmt = pg_insert(ChatParticipant).values(
            thread_id=thread_id,
            user_type=user_type,
            user_id=user_id,
            muted=muted,
            role=role,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["thread_id", "user_type", "user_id"],
            set_={"muted": muted},
        )
        await self._session.execute(stmt)

    async def archive_thread(self, thread_id: UUID) -> None:
        """Закрыть тред: status='closed' + closed_at=now() (архив/пустой)."""
        await self._session.execute(
            update(ChatThread)
            .where(ChatThread.id == thread_id)
            .values(status="closed", closed_at=func.now())
        )

    async def log_event(
        self,
        thread_id: UUID,
        actor: "ParticipantRef",
        event: str,
        payload: dict | None = None,
    ) -> None:
        """Записать аудит-событие треда (08 §5); actor — инициатор."""
        self._session.add(
            ChatEvent(
                thread_id=thread_id,
                actor_type=actor.user_type,
                actor_id=(
                    None
                    if actor.user_type in ("system", "bot")
                    else actor.user_id
                ),
                event=event,
                payload=payload,
            )
        )
        await self._session.flush()

    async def list_events(
        self, thread_id: UUID, *, limit: int = 100
    ) -> list[ChatEventView]:
        """Лента событий треда, новые сверху (id DESC, 08 §5)."""
        rows = (
            await self._session.execute(
                select(ChatEvent)
                .where(ChatEvent.thread_id == thread_id)
                .order_by(ChatEvent.id.desc())
                .limit(limit)
            )
        ).scalars().all()
        return [
            ChatEventView(
                id=row.id,
                thread_id=row.thread_id,
                actor_type=row.actor_type,
                actor_id=row.actor_id,
                event=row.event,
                payload=row.payload,
                created_at=row.created_at,
            )
            for row in rows
        ]

    async def create_report(
        self,
        thread_id: UUID,
        reporter: "ParticipantRef",
        reason: str,
        comment: str | None,
    ) -> int | None:
        """Вставить жалобу; None — «открытая» с тем же ключом уже есть.

        ON CONFLICT по частичному unique (thread, reporter, reason)
        WHERE status='new' — идемпотентность повторного POST (08 §4).
        """
        stmt = (
            pg_insert(ChatReport)
            .values(
                thread_id=thread_id,
                reporter_type=reporter.user_type,
                reporter_id=reporter.user_id,
                reason=reason,
                comment=comment,
            )
            .on_conflict_do_nothing(
                index_elements=[
                    "thread_id", "reporter_type", "reporter_id", "reason",
                ],
                index_where=sa_text("status = 'new'"),
            )
            .returning(ChatReport.id)
        )
        return (
            await self._session.execute(stmt)
        ).scalar_one_or_none()

    async def list_reports(
        self, status: str = "new", *, limit: int = 100
    ) -> list[AdminReportView]:
        """Жалобы по статусу + имя репортёра + сниппет последнего сообщения."""
        reports = (
            (
                await self._session.execute(
                    select(ChatReport, ChatThread)
                    .join(ChatThread, ChatReport.thread_id == ChatThread.id)
                    .where(ChatReport.status == status)
                    .order_by(ChatReport.created_at.desc())
                    .limit(limit)
                )
            )
            .all()
        )
        if not reports:
            return []
        thread_ids = [report.thread_id for report, _ in reports]
        last_map = await self._last_messages(thread_ids)
        names = await self._reporter_names(
            [(r.reporter_type, r.reporter_id) for r, _ in reports]
        )
        views: list[AdminReportView] = []
        for report, thread in reports:
            last = last_map.get(report.thread_id)
            views.append(
                AdminReportView(
                    id=report.id,
                    thread_id=report.thread_id,
                    reporter_type=report.reporter_type,
                    reporter_id=report.reporter_id,
                    reporter_name=names.get((report.reporter_type, report.reporter_id)),
                    reason=report.reason,
                    comment=report.comment,
                    status=report.status,
                    created_at=report.created_at,
                    handled_at=report.handled_at,
                    thread_title=thread.title,
                    thread_snippet=(
                        last.body_plain[:120] if last else None
                    ),
                )
            )
        return views

    async def handle_report(self, report_id: int, status: str) -> bool:
        """Отметить жалобу обработанной (resolved|dismissed) + handled_at."""
        result = await self._session.execute(
            update(ChatReport)
            .where(ChatReport.id == report_id)
            .values(status=status, handled_at=func.now())
        )
        return bool(result.rowcount)

    async def _reporter_names(
        self, refs: list[tuple[str, int]]
    ) -> dict[tuple[str, int], str | None]:
        """Отображаемые имена репортёров (users / partner_accounts)."""
        user_ids = [rid for rtype, rid in refs if rtype == "user"]
        partner_ids = [rid for rtype, rid in refs if rtype == "partner"]
        names: dict[tuple[str, int], str | None] = {}
        if user_ids:
            for key, name in (await self._user_names(user_ids)).items():
                names[("user", key)] = name
        if partner_ids:
            rows = (
                await self._session.execute(
                    select(PartnerAccount.id, PartnerAccount.name).where(
                        PartnerAccount.id.in_(partner_ids)
                    )
                )
            ).all()
            for partner_id, name in rows:
                names[("partner", partner_id)] = name
        return names


    async def list_participants(
        self, thread_id: UUID
    ) -> list[ParticipantView]:
        """Участники group-треда с именами: users LEFT JOIN partner_accounts.

        Строки participants двух сортов: user (telegram_id) и partner (id
        счёта) — имена тянутся каждый из своей таблицы LEFT JOIN'ами по
        условию типа строки (07 §2: name = first_name/username | счёт).
        """
        rows = (
            await self._session.execute(
                select(
                    ChatParticipant,
                    User.first_name,
                    User.username,
                    PartnerAccount.name,
                )
                .join(
                    User,
                    and_(
                        ChatParticipant.user_type == "user",
                        User.telegram_id == ChatParticipant.user_id,
                    ),
                    isouter=True,
                )
                .join(
                    PartnerAccount,
                    and_(
                        ChatParticipant.user_type == "partner",
                        PartnerAccount.id == ChatParticipant.user_id,
                    ),
                    isouter=True,
                )
                .where(ChatParticipant.thread_id == thread_id)
                .order_by(ChatParticipant.user_id)
            )
        ).all()
        views: list[ParticipantView] = []
        for participant, first_name, username, partner_name in rows:
            name = (
                (first_name or username)
                if participant.user_type == "user"
                else partner_name
            )
            views.append(
                ParticipantView(
                    user_type=participant.user_type,
                    user_id=participant.user_id,
                    name=name,
                    role=participant.role,
                )
            )
        return views

    async def get_participant_thread(
        self, thread_id: UUID, user_type: str, user_id: int
    ) -> ThreadRecord | None:
        """Тред для субъекта: владение (user/partner) или строка участника."""
        stmt = select(ChatThread).where(ChatThread.id == thread_id)
        if user_type == "user":
            stmt = stmt.where(
                or_(
                    ChatThread.user_id == user_id,
                    self._participant_exists(thread_id, "user", user_id),
                )
            )
        elif user_type == "partner":
            stmt = stmt.where(
                or_(
                    ChatThread.partner_id == user_id,
                    self._participant_exists(thread_id, "partner", user_id),
                )
            )
        else:
            stmt = stmt.where(
                self._participant_exists(thread_id, user_type, user_id)
            )
        thread = (
            (await self._session.execute(stmt)).scalars().one_or_none()
        )
        return _to_record(thread) if thread is not None else None

    async def is_participant(
        self, thread_id: UUID, user_type: str, user_id: int
    ) -> bool:
        """Быстрая проверка участия (subscribe-proxy)."""
        return await self.get_participant_thread(
            thread_id, user_type, user_id
        ) is not None

    async def get_thread(self, thread_id: UUID) -> ThreadRecord | None:
        """Тред по id без проверки доступа (оператор видит все, §2)."""
        thread = await self._session.get(ChatThread, thread_id)
        return _to_record(thread) if thread is not None else None

    async def pause_bot(self, thread_id: UUID) -> None:
        """Поставить бот на паузу (зов оператора, §6)."""
        await self._session.execute(
            update(ChatThread)
            .where(ChatThread.id == thread_id)
            .values(bot_paused=True)
        )

    # ------------------------------------------------------------------
    # Контур оператора (админка поддержки, §2/§6)
    # ------------------------------------------------------------------

    async def list_threads_for_operator(
        self,
        thread_type: str = "support",
        *,
        paused: bool | None = None,
        limit: int = 50,
    ) -> list[OperatorThreadView]:
        """Треды типа для оператора: юзер, последнее юзер-сообщение, пауза.

        Unread по участнику-оператору не считаем (строки оператора может
        не быть): сниппет последнего юзер-сообщения + `bot_paused` дают
        оператору ту же картину одним списком.
        """
        stmt = (
            select(ChatThread)
            .where(ChatThread.type == thread_type)
            .order_by(
                ChatThread.last_message_at.desc().nullslast(),
                ChatThread.created_at.desc(),
            )
            .limit(limit)
        )
        if paused is not None:
            stmt = stmt.where(ChatThread.bot_paused.is_(paused))
        threads = (
            (await self._session.execute(stmt)).scalars().all()
        )
        if not threads:
            return []
        last_user_map = await self._last_user_messages([t.id for t in threads])
        names = await self._user_names([t.user_id for t in threads])
        return [
            OperatorThreadView(
                id=thread.id,
                type=thread.type,
                status=thread.status,
                bot_paused=bool(thread.bot_paused),
                user_id=thread.user_id,
                user_name=names.get(thread.user_id),
                last_user_message_at=(
                    last_user_map[thread.id][0]
                    if thread.id in last_user_map
                    else None
                ),
                last_user_message_snippet=(
                    last_user_map[thread.id][1]
                    if thread.id in last_user_map
                    else None
                ),
            )
            for thread in threads
        ]

    async def _last_user_messages(
        self, thread_ids: list[UUID]
    ) -> dict[UUID, tuple[datetime, str]]:
        """`(created_at, сниппет)` последнего юзер-сообщения каждого треда."""
        latest = (
            select(
                ChatMessage.thread_id,
                func.max(ChatMessage.id).label("max_id"),
            )
            .where(
                ChatMessage.thread_id.in_(thread_ids),
                ChatMessage.sender_type == "user",
            )
            .group_by(ChatMessage.thread_id)
            .subquery()
        )
        rows = (
            await self._session.execute(
                select(ChatMessage.thread_id, ChatMessage.body, ChatMessage.created_at)
                .join(latest, ChatMessage.id == latest.c.max_id)
            )
        ).all()
        return {
            thread_id: (created_at, plain_text(body)[:120])
            for thread_id, body, created_at in rows
        }

    async def _user_names(self, user_ids: list[int]) -> dict[int, str]:
        """Отображаемые имена юзеров тредов (username → first_name fallback)."""
        if not user_ids:
            return {}
        rows = (
            await self._session.execute(
                select(
                    User.telegram_id,
                    User.username,
                    User.first_name,
                ).where(User.telegram_id.in_(user_ids))
            )
        ).all()
        return {
            tg_id: (username or first_name)
            for tg_id, username, first_name in rows
        }

    async def count_awaiting_operator(self, thread_type: str = "support") -> int:
        """Треды, где юзер писал позже последнего сообщения оператора.

        Коррелированный подзапрос «последнее сообщение оператора треда»
        (NULL → 0: операторов не было) — прочитанным считается только
        ответ оператора, бот паузу не гасит (§6).
        """
        last_operator = (
            select(func.max(ChatMessage.id))
            .where(
                ChatMessage.thread_id == ChatThread.id,
                ChatMessage.sender_type == "operator",
            )
            .correlate(ChatThread)
            .scalar_subquery()
        )
        awaiting = (
            select(literal(1))
            .where(
                ChatMessage.thread_id == ChatThread.id,
                ChatMessage.sender_type == "user",
                ChatMessage.id > func.coalesce(last_operator, 0),
            )
            .correlate(ChatThread)
            .exists()
        )
        return int(
            (
                await self._session.execute(
                    select(func.count(ChatThread.id)).where(
                        ChatThread.type == thread_type,
                        awaiting,
                    )
                )
            ).scalar()
            or 0
        )

    @staticmethod
    def _participant_exists(
        thread_id: UUID, user_type: str, user_id: int
    ) -> object:
        """EXISTS-подзапрос строки участника."""
        return select(literal(1)).where(
            ChatParticipant.thread_id == thread_id,
            ChatParticipant.user_type == user_type,
            ChatParticipant.user_id == user_id,
        ).exists()


    # ------------------------------------------------------------------
    # Списки тредов (с unread/last_message/subject)
    # ------------------------------------------------------------------

    async def list_threads_for_user(self, user_id: int) -> list[ThreadView]:
        """Треды юзера: владение или строка участника (user).

        Retention (08 §5): закрытые треды старше 90 дней скрыты.
        """
        marker, role_sq, muted_sq = self._participant_columns("user", user_id)
        unread = self._unread_subquery("user", user_id, marker)
        stmt = (
            select(ChatThread, unread, role_sq, muted_sq)
            .where(
                self._retention_filter(),
                or_(
                    ChatThread.user_id == user_id,
                    self._participant_exists_sql("user", user_id),
                ),
            )
            .order_by(
                ChatThread.last_message_at.desc().nullslast(),
                ChatThread.created_at.desc(),
            )
        )
        return await self._collect_views(
            stmt, viewer_type="user", viewer_id=user_id
        )

    async def list_threads_for_partner(
        self, partner_id: int
    ) -> list[ThreadView]:
        """Треды счёта: seller/deal по partner_id + group по участию (07 §2).

        Retention (08 §5): закрытые треды старше 90 дней скрыты.
        """
        marker, role_sq, muted_sq = self._participant_columns(
            "partner", partner_id
        )
        unread = self._unread_subquery("partner", partner_id, marker)
        stmt = (
            select(ChatThread, unread, role_sq, muted_sq)
            .where(
                self._retention_filter(),
                or_(
                    and_(
                        ChatThread.partner_id == partner_id,
                        ChatThread.type.in_(("seller", "deal")),
                    ),
                    # Переговорная: партнёр видит только те, куда его
                    # позвали кодом (участник partner).
                    and_(
                        ChatThread.type == "group",
                        self._participant_exists_sql("partner", partner_id),
                    ),
                ),
            )
            .order_by(
                ChatThread.last_message_at.desc().nullslast(),
                ChatThread.created_at.desc(),
            )
        )
        return await self._collect_views(
            stmt, viewer_type="partner", viewer_id=partner_id
        )

    @staticmethod
    def _retention_filter():
        """Скрыть закрытые треды старше 90 дней (08 §5, soft-hide).

        closed_at IS NULL у тредов, закрытых до 0029 — они остаются
        видимыми (точки отсчёта нет, данные не выбрасываем).
        """
        cutoff = datetime.now(UTC) - timedelta(days=CHAT_RETENTION_DAYS)
        return or_(
            ChatThread.status != "closed",
            ChatThread.closed_at.is_(None),
            ChatThread.closed_at >= cutoff,
        )

    def _participant_columns(self, user_type: str, user_id: int):
        """Подзапросы строки участника: (last_read, role, muted) — 08 §2/§6.

        Расширение marker-подзапроса (05 §3) говернансом: роль и mute
        берём той же коррелированной точкой по chat_threads — отдельный
        запрос на свою participant-строку не нужен. Явная корреляция с
        ChatThread обязательна (см. `_marker_subquery` выше по истории).
        """
        def _column(column):
            return (
                select(column)
                .where(
                    ChatParticipant.thread_id == ChatThread.id,
                    ChatParticipant.user_type == user_type,
                    ChatParticipant.user_id == user_id,
                )
                .correlate(ChatThread)
                .scalar_subquery()
            )

        return (
            _column(ChatParticipant.last_read_message_id),
            _column(ChatParticipant.role),
            _column(ChatParticipant.muted),
        )

    def _unread_subquery(self, user_type: str, user_id: int, marker):
        """COUNT сообщений после маркера, кроме собственных.

        System-строки считаются непрочитанными всем участникам: экран
        чата сразу зовёт mark_read, поэтому приветствие гаснет при
        первом открытии.
        """
        not_own = or_(
            ChatMessage.sender_type != user_type,
            ChatMessage.sender_id != user_id,
        )
        return (
            select(func.count(ChatMessage.id))
            .where(
                ChatMessage.thread_id == ChatThread.id,
                ChatMessage.id > func.coalesce(marker, 0),
                not_own,
            )
            .correlate(ChatThread)
            .scalar_subquery()
        )

    def _participant_exists_sql(self, user_type: str, user_id: int):
        """EXISTS участника для внешнего запроса (корреляция по thread_id)."""
        return select(literal(1)).where(
            ChatParticipant.thread_id == ChatThread.id,
            ChatParticipant.user_type == user_type,
            ChatParticipant.user_id == user_id,
        ).exists()

    async def _collect_views(
        self, stmt, *, viewer_type: str, viewer_id: int
    ) -> list[ThreadView]:
        """Выполнить список тредов + догрузка last_message/subject/лид.

        Строка результата: (thread, unread, role, muted) — role/muted из
        подзапросов participant-строки зрителя (08 §2/§6); без строки —
        роль владельца для своих service-тредов (thread.user_id /
        thread.partner_id), иначе 'member'.
        """
        rows = (await self._session.execute(stmt)).all()
        threads = [row[0] for row in rows]
        unreads = [int(row[1] or 0) for row in rows]
        if not threads:
            return []
        last_map = await self._last_messages([t.id for t in threads])
        subject_map = await self._subjects(
            [t.listing_id for t in threads if t.listing_id is not None]
        )
        lead_map = await self._lead_statuses(
            [t.lead_id for t in threads if t.lead_id is not None]
        )
        # Участники переговорных (07 §2): имена для шапки комнаты, одним
        # запросом на тред (группы, влезающие в список, — редкий случай).
        participant_map: dict[UUID, tuple[ParticipantView, ...]] = {}
        for thread in threads:
            if thread.type == "group":
                participant_map[thread.id] = tuple(
                    await self.list_participants(thread.id)
                )
        return [
            _to_view(
                thread,
                subject_map.get(thread.listing_id),
                unread,
                last_map.get(thread.id),
                lead_map.get(thread.lead_id),
                participant_map.get(thread.id, ()),
                role=self._default_role(
                    thread, viewer_type, viewer_id, row[2]
                ),
                muted=bool(row[3]),
            )
            for row, thread, unread in zip(rows, threads, unreads)
        ]

    @staticmethod
    def _default_role(
        thread: ChatThread,
        viewer_type: str,
        viewer_id: int,
        participant_role: str | None,
    ) -> str:
        """Своя роль: строка участника, иначе владелец service-треда."""
        if participant_role is not None:
            return participant_role
        if (
            viewer_type == "user" and thread.user_id == viewer_id
        ) or (
            viewer_type == "partner" and thread.partner_id == viewer_id
        ):
            return "owner"
        return "member"

    async def _lead_statuses(self, lead_ids: list[int]) -> dict[int, str | None]:
        """Партнёрские статусы лидов deal-тредов (одним запросом)."""
        if not lead_ids:
            return {}
        rows = (
            await self._session.execute(
                select(Lead.id, Lead.partner_status).where(
                    Lead.id.in_(lead_ids)
                )
            )
        ).all()
        return {lead_id: status for lead_id, status in rows}

    async def _last_messages(self, thread_ids: list[UUID]) -> dict:
        """Последнее сообщение каждого треда (одним запросом)."""
        latest = (
            select(
                ChatMessage.thread_id,
                func.max(ChatMessage.id).label("max_id"),
            )
            .where(ChatMessage.thread_id.in_(thread_ids))
            .group_by(ChatMessage.thread_id)
            .subquery()
        )
        stmt = select(ChatMessage).join(
            latest, ChatMessage.id == latest.c.max_id
        )
        messages = (await self._session.execute(stmt)).scalars().all()
        return {
            m.thread_id: LastMessageView(
                body_plain=plain_text(m.body),
                created_at=m.created_at,
                sender_type=m.sender_type,
                sender_id=m.sender_id,
            )
            for m in messages
        }

    async def _subjects(self, listing_ids: list[int]) -> dict[int, SubjectCard]:
        """Мини-карточки предметов тредов (одним запросом по listings)."""
        if not listing_ids:
            return {}
        rows = (
            await self._session.execute(
                select(Listing).where(Listing.id.in_(listing_ids))
            )
        ).scalars().all()
        cards: dict[int, SubjectCard] = {}
        for row in rows:
            images = row.images or []
            cards[row.id] = SubjectCard(
                id=row.id,
                make=row.make,
                model=row.model,
                year=row.year,
                price=float(row.price) if row.price is not None else None,
                currency=row.currency,
                url=row.url,
                image=images[0] if images else None,
            )
        return cards

    # ------------------------------------------------------------------
    # Сообщения и read-маркеры
    # ------------------------------------------------------------------

    async def messages(
        self,
        thread_id: UUID,
        *,
        before_id: int | None = None,
        after_id: int | None = None,
        limit: int = 50,
    ) -> list[MessageView]:
        """Сообщения по возрастанию id (§5: before_id вниз, after_id поллинг)."""
        stmt = select(ChatMessage).where(ChatMessage.thread_id == thread_id)
        if after_id is not None:
            stmt = stmt.where(ChatMessage.id > after_id).order_by(
                ChatMessage.id.asc()
            )
        else:
            if before_id is not None:
                stmt = stmt.where(ChatMessage.id < before_id)
            # Новые сверху → переворачиваем в хронологию для ответа.
            stmt = stmt.order_by(ChatMessage.id.desc())
        rows = (
            (await self._session.execute(stmt.limit(limit))).scalars().all()
        )
        messages = [_message_view(m) for m in rows]
        if after_id is None:
            messages.reverse()
        return messages

    async def add_message(self, command: SendMessageCommand) -> MessageView:
        """Записать сообщение; last_message_at + bot_paused (§6)."""
        message = ChatMessage(
            thread_id=command.thread_id,
            sender_type=command.sender.user_type,
            sender_id=(
                None
                if command.sender.user_type in ("system", "bot")
                else command.sender.user_id
            ),
            body=command.body,
        )
        self._session.add(message)
        await self._session.flush()
        thread = await self._session.get(
            ChatThread, command.thread_id, with_for_update=False
        )
        if thread is not None:
            thread.last_message_at = message.created_at
            if command.sender.user_type == "operator":
                # Оператор вклинился — бот молчит, пока юзер не напишет (§2).
                thread.bot_paused = True
            elif command.sender.user_type == "user" and thread.bot_paused:
                thread.bot_paused = False
            await self._session.flush()
        return _message_view(message)

    async def max_message_id(self, thread_id: UUID) -> int:
        """Последний id треда (0 — сообщений нет)."""
        return int(
            (
                await self._session.execute(
                    select(func.max(ChatMessage.id)).where(
                        ChatMessage.thread_id == thread_id
                    )
                )
            ).scalar()
            or 0
        )

    async def count_messages(
        self, thread_id: UUID, *, sender_type: str, since: datetime
    ) -> int:
        """Сообщения отправителя с момента `since` (окно лимита бота, §6)."""
        return int(
            (
                await self._session.execute(
                    select(func.count(ChatMessage.id)).where(
                        ChatMessage.thread_id == thread_id,
                        ChatMessage.sender_type == sender_type,
                        ChatMessage.created_at >= since,
                    )
                )
            ).scalar()
            or 0
        )

    async def attach_partner_to_deal(
        self, lead_id: int, partner_id: int
    ) -> ThreadRecord | None:
        """Привязать партнёра к deal-треду лида (claim, §1); None — треда нет."""
        thread = (
            (
                await self._session.execute(
                    select(ChatThread).where(
                        ChatThread.type == "deal",
                        ChatThread.lead_id == lead_id,
                    )
                )
            )
            .scalars()
            .one_or_none()
        )
        if thread is None:
            return None
        thread.partner_id = partner_id
        await self._session.flush()
        return _to_record(thread)

    async def mark_read(self, command: MarkReadCommand) -> None:
        """Поднять read-маркер участника монотонно (greatest, без отката)."""
        stmt = pg_insert(ChatParticipant).values(
            thread_id=command.thread_id,
            user_type=command.reader.user_type,
            user_id=command.reader.user_id,
            last_read_message_id=command.last_read_message_id,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["thread_id", "user_type", "user_id"],
            set_={
                "last_read_message_id": func.greatest(
                    ChatParticipant.__table__.c.last_read_message_id,
                    stmt.excluded.last_read_message_id,
                )
            },
        )
        await self._session.execute(stmt)


def _message_view(message: ChatMessage) -> MessageView:
    """ORM-сообщение → MessageView."""
    return MessageView(
        id=message.id,
        sender_type=message.sender_type,
        sender_id=message.sender_id,
        body=message.body,
        created_at=message.created_at,
    )
