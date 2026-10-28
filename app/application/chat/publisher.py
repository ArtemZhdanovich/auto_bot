"""Единая точка публикации событий треда: личные каналы участников (09 §1).

Клиент подписывается только на свой канал `user:{telegram_id}` /
`partner:{id}` — публикаций в `thread:{id}` больше нет. Обёртка
`ParticipantEventPublisher` собирает участников треда (владелец-юзер,
партнёр счёта, строки chat_participants) и рассылает копию события
в уникальные личные каналы каждого.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from app.application.chat.dto import ParticipantRef, ThreadRecord
from app.application.chat.ports import ChatPublisher, ChatRepository, ThreadEventPublisher

logger = logging.getLogger(__name__)

# Типы субъектов с личным каналом centrifugo: user → user:{telegram_id},
# partner → partner:{id}. У operator/system/bot личного канала нет
# (админ-контур ходит поллингом) — их строки участников пропускаем.
_CHANNELLED_TYPES = ("user", "partner")


def channel_for(user_type: str, user_id: int) -> str:
    """Личный канал субъекта: `user:{tg}` / `partner:{id}` (09 §1)."""
    return f"{user_type}:{user_id}"


class ParticipantEventPublisher(ThreadEventPublisher):
    """`ThreadEventPublisher`: событие треда → личные каналы участников.

    Владелец берётся из записи треда (`user_id` + `partner_id`), остальные
    — из строк `chat_participants` (group-треды и seller-треды двух
    юзеров). Каналы дедуплицируются; каждому участнику уходит своя копия
    payload. Если в событии нет `thread_id` — добавляется (обратная
    совместимость полезной нагрузки, 09 §1).
    """

    def __init__(self, chats: ChatRepository, publisher: ChatPublisher) -> None:
        self._chats = chats
        self._publisher = publisher

    async def publish_to_participants(
        self,
        thread_id: UUID,
        event: dict[str, Any],
        *,
        exclude: ParticipantRef | None = None,
    ) -> None:
        """Разослать событие участникам треда в их личные каналы.

        Args:
            thread_id: Тред, чьё событие уходит участникам.
            event: Payload `{type, ...}`; `thread_id` добавится при
                отсутствии.
            exclude: Участник, которому НЕ слать (typing — свой echo
                не нужен, 09 §1).
        """
        thread = await self._chats.get_thread(thread_id)
        if thread is None:
            # Тред исчез (гонка удаления) — событие терять не страшно:
            # клиент доедет поллингом списков.
            logger.warning(
                "publish_to_participants: thread not found thread=%s", thread_id
            )
            return
        channels = await self._participant_channels(thread, exclude)
        if not channels:
            return
        payload = dict(event)
        payload.setdefault("thread_id", str(thread_id))
        for channel in channels:
            # Копия каждому: каналы не делят один dict (мутации изолированы).
            await self._publisher.publish(channel, dict(payload))

    async def _participant_channels(
        self, thread: ThreadRecord, exclude: ParticipantRef | None
    ) -> list[str]:
        """Уникальные личные каналы участников треда в стабильном порядке.

        Владелец-юзер и партнёр счёта — первыми (даже без строки участника),
        затем строки `chat_participants`; дубликанты схлопываются.
        """
        refs: list[tuple[str, int]] = [("user", thread.user_id)]
        if thread.partner_id is not None:
            refs.append(("partner", thread.partner_id))
        for participant in await self._chats.list_participants(thread.id):
            if participant.user_type in _CHANNELLED_TYPES:
                refs.append((participant.user_type, participant.user_id))
        channels: list[str] = []
        seen: set[str] = set()
        for user_type, user_id in refs:
            if exclude is not None and (user_type, user_id) == (
                exclude.user_type,
                exclude.user_id,
            ):
                continue
            channel = channel_for(user_type, user_id)
            if channel not in seen:
                seen.add(channel)
                channels.append(channel)
        return channels


__all__ = ["ParticipantEventPublisher", "channel_for"]
