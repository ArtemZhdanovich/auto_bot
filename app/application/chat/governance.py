"""Говернанс переговорных (08 §2, §4-§6): kick/leave/archive, mute, жалобы.

Отдельный модуль от базовых сценариев чатов: операции говернанса пишут
аудит-события (chat_events) и завязаны на роли участников (0029).
Правила доступа едины: участник треда + роль из строки участника
(`_actor_role`), операции только с group-тредами (кроме mute — свои
уведомления настраиваются в любом треде, 08 §6).
"""

from __future__ import annotations

import logging
from uuid import UUID

from app.application.chat.dto import (
    AdminReportView,
    ChatEventView,
    ParticipantRef,
    SendMessageCommand,
    ThreadRecord,
)
from app.application.chat.ports import ChatRepository, ThreadEventPublisher
from app.application.chat.use_cases import (
    CHAT_NOT_FOUND,
    NOT_A_GROUP_THREAD,
    _actor_role,
    _system_sender,
    message_event,
)
from app.application.errors import Conflict, Forbidden, InvalidInput, NotFound
from app.application.shared.ports import UnitOfWork

logger = logging.getLogger(__name__)

# §2: kick/leave/archive.
KICK_OWNER_ONLY = "kick_owner_only: исключать участников может только владелец"
CANNOT_KICK_OWNER = "cannot_kick_owner: владельца исключить нельзя"
KICK_SELF_LEAVE = (
    "cannot_kick_self: чтобы выйти из переговорной, используйте leave"
)
OWNER_MUST_ARCHIVE = (
    "owner_must_archive: владелец уходит последним — сначала архивируйте "
    "переговорную"
)
ARCHIVE_OWNER_ONLY = (
    "archive_owner_only: архивировать может только владелец переговорной"
)
PARTICIPANT_NOT_FOUND = "participant_not_found: участник не найден"
THREAD_ARCHIVED_MESSAGE = "Комната архивирована"

# §4: жалобы.
REPORT_REASONS = ("spam", "fraud", "offtop", "abuse")
REPORT_INVALID = "report_invalid: причина жалобы — spam | fraud | oftop | abuse"
REPORT_STATUS_INVALID = (
    "report_status_invalid: статус обработки — resolved | dismissed"
)
REPORT_NOT_FOUND = "report_not_found: жалоба не найдена"

# Служебный субъект оператора (админ-контур без персонального identity).
_OPERATOR = ParticipantRef(user_type="operator", user_id=0)


async def _participant_thread(
    chats: ChatRepository, thread_id: UUID, subject: ParticipantRef
) -> ThreadRecord:
    """Тред субъекта или 404 (единая проверка доступа операций §2)."""
    thread = await chats.get_participant_thread(
        thread_id, subject.user_type, subject.user_id
    )
    if thread is None:
        raise NotFound(CHAT_NOT_FOUND)
    return thread


def _require_group(thread: ThreadRecord) -> None:
    """Операции говернанса §2 — только для переговорных."""
    if thread.type != "group":
        raise Conflict(NOT_A_GROUP_THREAD)


class KickParticipant:
    """DELETE /api/chats/{id}/participants/{user_type}/{user_id} (§2).

    Owner кикает member любого типа (user/partner); кик owner — 409
    `cannot_kick_owner`, кик себя — 409 с подсказкой использовать leave.
    Партнёрская строка кикается так же (partner-зеркало участников).
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
        self,
        thread_id: UUID,
        requester: ParticipantRef,
        target_type: str,
        target_id: int,
    ) -> None:
        """Исключить участника; событие 'kicked' с actor=инициатор.

        Raises:
            NotFound: Тред недоступен / цели нет среди участников.
            Forbidden: Инициатор не владелец.
            Conflict: Не group-тред; цель — owner; кик себя.
        """
        thread = await _participant_thread(self._chats, thread_id, requester)
        _require_group(thread)
        if (requester.user_type, requester.user_id) == (
            target_type,
            target_id,
        ):
            raise Conflict(KICK_SELF_LEAVE)
        if await _actor_role(self._chats, thread, requester) != "owner":
            raise Forbidden(KICK_OWNER_ONLY)
        target_role = await self._chats.participant_role(
            thread_id, target_type, target_id
        )
        if target_role is None:
            raise NotFound(PARTICIPANT_NOT_FOUND)
        if target_role == "owner":
            raise Conflict(CANNOT_KICK_OWNER)
        await self._chats.remove_participant(thread_id, target_type, target_id)
        await self._chats.log_event(
            thread_id,
            requester,
            "kicked",
            {"user_type": target_type, "user_id": target_id},
        )
        await self._uow.commit()
        # 09 §1: участники узнают об изменении состава мгновенно —
        # system-событие в личные каналы (кикнутому тоже: его стор
        # должен убрать тред).
        await self._publisher.publish_to_participants(
            thread_id,
            {
                "type": "system",
                "event": "kicked",
                "user_type": target_type,
                "user_id": target_id,
            },
        )
        logger.info(
            "participant_kicked thread=%s target=%s:%s by=%s:%s",
            thread_id,
            target_type,
            target_id,
            requester.user_type,
            requester.user_id,
        )


class LeaveThread:
    """POST /api/chats/{id}/leave (§2): участник выходит из переговорной.

    Member уходит свободно; owner — только если он последний участник
    (иначе 409 `owner_must_archive` — сначала «Архивировать»). Когда
    участников 0 — тред закрывается (status='closed', closed_at=now).
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
        self, thread_id: UUID, requester: ParticipantRef
    ) -> None:
        """Удалить свою строку участника + событие 'left'.

        Raises:
            NotFound: Тред недоступен субъекту.
            Conflict: Не group-тред; owner не последний (owner_must_archive).
        """
        thread = await _participant_thread(self._chats, thread_id, requester)
        _require_group(thread)
        participants = await self._chats.count_participants(thread_id)
        if (
            await _actor_role(self._chats, thread, requester) == "owner"
            and participants > 1
        ):
            raise Conflict(OWNER_MUST_ARCHIVE)
        await self._chats.remove_participant(
            thread_id, requester.user_type, requester.user_id
        )
        await self._chats.log_event(thread_id, requester, "left")
        if participants <= 1:
            # Последний участник вышел — тред закрывается сам (§2).
            await self._chats.archive_thread(thread_id)
        await self._uow.commit()
        # 09 §1: уход участника — system-событие в личные каналы
        # (ушедшему тоже: тред пропадает из его списка).
        await self._publisher.publish_to_participants(
            thread_id,
            {
                "type": "system",
                "event": "left",
                "user_type": requester.user_type,
                "user_id": requester.user_id,
            },
        )
        logger.info(
            "participant_left thread=%s by=%s:%s",
            thread_id,
            requester.user_type,
            requester.user_id,
        )


class ArchiveThread:
    """POST /api/chats/{id}/archive (§2): owner закрывает переговорную.

    status='closed' (отправка сообщений → 403 thread_archived) +
    closed_at (точка отсчёта retention §5) + system-сообщение
    «Комната архивирована». Идемпотентно: повторный archive закрытого
    треда — 200 без второй system-строки и события.
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
        self, thread_id: UUID, requester: ParticipantRef
    ) -> bool:
        """Заархивировать; `False` — уже закрыт (идемпотентный повтор).

        Raises:
            NotFound: Тред недоступен субъекту.
            Forbidden: Инициатор не владелец.
            Conflict: Не group-тред.
        """
        thread = await _participant_thread(self._chats, thread_id, requester)
        _require_group(thread)
        if thread.status == "closed":
            return False
        if await _actor_role(self._chats, thread, requester) != "owner":
            raise Forbidden(ARCHIVE_OWNER_ONLY)
        await self._chats.archive_thread(thread_id)
        message = await self._chats.add_message(
            SendMessageCommand(
                thread_id=thread_id,
                sender=_system_sender(),
                body=THREAD_ARCHIVED_MESSAGE,
            )
        )
        await self._chats.log_event(thread_id, requester, "archived")
        await self._uow.commit()
        # 09 §1: system-строка «Комната архивирована» — полное сообщение
        # (клиент рендерит без докачки) + meta-событие о статусе треда.
        await self._publisher.publish_to_participants(
            thread_id, message_event(thread_id, message)
        )
        await self._publisher.publish_to_participants(
            thread_id, {"type": "system", "event": "archived"}
        )
        logger.info(
            "thread_archived thread=%s by=%s:%s",
            thread_id,
            requester.user_type,
            requester.user_id,
        )
        return True


class ToggleMute:
    """POST /api/chats/{id}/mute | /unmute (§6): свои уведомления.

    Свой muted в строке участника (upsert: строки может не быть) +
    событие muted/unmuted в аудит. Работает в тредах любого типа —
    mute не меняет права, только свой бейдж конверта.
    """

    def __init__(self, chats: ChatRepository, uow: UnitOfWork) -> None:
        self._chats = chats
        self._uow = uow

    async def execute(
        self, thread_id: UUID, requester: ParticipantRef, muted: bool
    ) -> bool:
        """Поставить/снять mute; возвращает итоговое состояние.

        Raises:
            NotFound: Тред недоступен субъекту.
        """
        thread = await _participant_thread(self._chats, thread_id, requester)
        # Роль при upsert-вставке новой строки: владельцу — 'owner',
        # чтобы mute не «разжаловал» его в списке чатов.
        role = await _actor_role(self._chats, thread, requester)
        await self._chats.set_muted(
            thread_id,
            requester.user_type,
            requester.user_id,
            muted,
            role=role,
        )
        await self._chats.log_event(
            thread_id, requester, "muted" if muted else "unmuted"
        )
        await self._uow.commit()
        return muted


class ReportThread:
    """POST /api/chats/{id}/report (§4): жалоба участника на тред.

    Идемпотентна, пока жалоба «открыта»: повтор с той же причиной →
    `False` (HTTP 200) без новой строки (partial unique 0029); после
    обработки (resolved/dismissed) пожаловаться можно снова.
    """

    def __init__(self, chats: ChatRepository, uow: UnitOfWork) -> None:
        self._chats = chats
        self._uow = uow

    async def execute(
        self,
        thread_id: UUID,
        reporter: ParticipantRef,
        reason: str,
        comment: str | None = None,
    ) -> bool:
        """Создать жалобу + событие 'reported'; `False` — дубль.

        Raises:
            NotFound: Тред недоступен субъекту (только участники).
            InvalidInput: Причина вне spam | fraud | oftop | abuse.
        """
        if reason not in REPORT_REASONS:
            raise InvalidInput(REPORT_INVALID)
        await _participant_thread(self._chats, thread_id, reporter)
        report_id = await self._chats.create_report(
            thread_id, reporter, reason, comment
        )
        if report_id is None:
            # «Открытая» жалоба с тем же ключом уже есть — 200 (§4).
            return False
        await self._chats.log_event(
            thread_id,
            reporter,
            "reported",
            {"reason": reason, "report_id": report_id},
        )
        await self._uow.commit()
        logger.info(
            "chat_reported thread=%s by=%s:%s reason=%s",
            thread_id,
            reporter.user_type,
            reporter.user_id,
            reason,
        )
        return True


class ListAdminReports:
    """GET /api/admin/chats/reports?status= (§4): список жалоб модерации."""

    def __init__(self, chats: ChatRepository) -> None:
        self._chats = chats

    async def execute(
        self, status: str = "new", *, limit: int = 100
    ) -> list[AdminReportView]:
        """Жалобы по статусу: + имя репортёра и сниппет треда."""
        return await self._chats.list_reports(status, limit=limit)


class HandleReport:
    """PATCH /api/admin/chats/reports/{id} (§4): обработка жалобы."""

    def __init__(self, chats: ChatRepository, uow: UnitOfWork) -> None:
        self._chats = chats
        self._uow = uow

    async def execute(self, report_id: int, status: str) -> None:
        """Пометить resolved|dismissed (handled_at=now).

        Raises:
            NotFound: Жалобы нет.
            InvalidInput: Статус вне resolved | dismissed.
        """
        if status not in ("resolved", "dismissed"):
            raise InvalidInput(REPORT_STATUS_INVALID)
        if not await self._chats.handle_report(report_id, status):
            raise NotFound(REPORT_NOT_FOUND)
        await self._uow.commit()
        logger.info("chat_report_handled id=%s status=%s", report_id, status)


class AdminKick:
    """DELETE /api/admin/chats/{thread}/participants/... (§4): кик оператора.

    Оператор (internal-контур) может исключить любого участника, включая
    owner. Событие 'kicked' с actor=operator (user_id=0 — персонального
    identity у админ-контура пока нет, MVP).
    """

    def __init__(self, chats: ChatRepository, uow: UnitOfWork) -> None:
        self._chats = chats
        self._uow = uow

    async def execute(
        self, thread_id: UUID, target_type: str, target_id: int
    ) -> None:
        """Исключить участника треда (без проверки роли инициатора).

        Raises:
            NotFound: Треда нет / цели нет среди участников.
        """
        if await self._chats.get_thread(thread_id) is None:
            raise NotFound(CHAT_NOT_FOUND)
        if not await self._chats.remove_participant(
            thread_id, target_type, target_id
        ):
            raise NotFound(PARTICIPANT_NOT_FOUND)
        await self._chats.log_event(
            thread_id,
            _OPERATOR,
            "kicked",
            {"user_type": target_type, "user_id": target_id},
        )
        await self._uow.commit()
        logger.info(
            "admin_kick thread=%s target=%s:%s",
            thread_id,
            target_type,
            target_id,
        )


class ListThreadEvents:
    """GET /api/admin/chats/{id}/events (§5): лента аудита, новые сверху."""

    def __init__(self, chats: ChatRepository) -> None:
        self._chats = chats

    async def execute(
        self, thread_id: UUID, *, limit: int = 100
    ) -> list[ChatEventView]:
        """События треда (id DESC); треда нет — 404.

        Raises:
            NotFound: Треда нет.
        """
        if await self._chats.get_thread(thread_id) is None:
            raise NotFound(CHAT_NOT_FOUND)
        return await self._chats.list_events(thread_id, limit=limit)
