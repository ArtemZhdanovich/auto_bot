"""Заявки по объявлению: агент на сделку (Business) и «свяжитесь со мной».

Business-пользователь (перекуп/маклер) может запросить агента на осмотр
и оформление сделки: создаётся лид с source="agent" — попадает в пул
партнёров (claim) и в админ-контур. Правило «только Business» живёт
здесь, транспорт только передаёт пользователя.

`RequestContact` — монетизация для ЛЮБОГО тарифа: лид source="web" из
кнопки «Связаться с партнёром/менеджером» уходит в пул партнёров (CPL).
Гость может только после входа (контакт требует реального аккаунта),
а дубли прижимаются гейтом «1 заявка на объявление в сутки».
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import Protocol
from uuid import UUID

from app.application.catalog.ports import UserLookupPort
from app.application.chat.ports import ChatRepository
from app.application.errors import Conflict, Forbidden, NotFound
from app.application.leads.use_cases import CreateLead
from app.application.partners.dto import LeadView
from app.application.shared.ports import UnitOfWork
from app.domain.subscription.policies import TIERS

logger = logging.getLogger(__name__)

# Окно rate-гейта «свяжитесь со мной»: не чаще одной заявки на объявление.
CONTACT_WINDOW_HOURS = 24

# Тексты для клиента: транспорт отдаёт их как есть (detail HTTP-ошибки).
CONTACT_GUEST_FORBIDDEN = (
    "guest_forbidden: заявка доступна после входа или регистрации"
)
CONTACT_DUPLICATE = (
    "duplicate_contact: заявка по этому автомобилю уже отправлена сегодня"
)
CONTACT_DEFAULT_COMMENT = "Интерес к автомобилю: свяжитесь со мной"

# System-сообщение в deal-тред при создании заявки на агента (§1, §5).
AGENT_DEAL_CREATED = "Заявка #{lead_id} на агента создана — агент подключится к этому чату"


class ContactRateGate(Protocol):
    """Rate-гейт заявок «свяжитесь со мной» (окно 24ч, sent_log kind='contact').

    Реализация — на sent_log: PK (user_id, listing_id, kind) даёт одну
    строку на пару пользователь+объявление, окно — по `sent_at`.
    """

    async def recently_sent(self, user_id: int, listing_id: int) -> bool:
        """Была ли заявка по этому объявлению за последние 24 часа."""

    async def mark_sent(self, user_id: int, listing_id: int) -> None:
        """Зафиксировать заявку (обновляет `sent_at`, окно стартует заново)."""


@dataclass(frozen=True, slots=True)
class AgentRequestView:
    """Итог заявки на агента: лид + deal-тред под переписку (§1, §5)."""

    lead: LeadView
    thread_id: UUID | None = None
    thread_created: bool = False


class RequestAgent:
    """Создать заявку на агента по объявлению (Business only).

    Вместе с лидом заводится deal-тред (reuse по `lead_id`, §1): заявка
    живёт в чатах — туда же подключается партнёр после claim. Чаты
    опциональны (`None` → только лид), чтобы сценарий работал и там,
    где контур чатов не собран.
    """

    def __init__(
        self,
        users: UserLookupPort,
        create_lead: CreateLead,
        uow: UnitOfWork,
        chats: ChatRepository | None = None,
    ) -> None:
        self._users = users
        self._create_lead = create_lead
        self._uow = uow
        self._chats = chats

    async def execute(self, user_id: int, listing_id: int, comment: str | None = None) -> AgentRequestView:
        """Заявка на агента; возвращает лид и deal-тред.

        Raises:
            NotFound: Пользователя нет.
            Forbidden: Тариф без права агента (не Business).
        """
        from app.application.chat.dto import ParticipantRef, SendMessageCommand
        from app.application.partners.dto import CreateLeadCommand

        profile = await self._users.get(user_id)
        if profile is None:
            raise NotFound("User not found")
        if not TIERS.get(profile.tier, TIERS["free"]).agent:
            raise Forbidden(
                "agent_forbidden: отправка агента доступна на тарифе Business"
            )
        note = comment or "Заявка на агента: осмотр и оформление сделки"
        lead = await self._create_lead.execute(
            CreateLeadCommand(
                user_id=user_id,
                listing_id=listing_id,
                source="agent",
                comment=note,
                idempotency_key=f"agent:{user_id}:{listing_id}",
            )
        )
        thread_id: UUID | None = None
        thread_created = False
        if self._chats is not None:
            thread, thread_created = await self._chats.get_or_create_thread(
                type="deal",
                user_id=user_id,
                lead_id=lead.id,
                partner_id=None,
            )
            thread_id = thread.id
            if thread_created:
                await self._chats.add_message(
                    SendMessageCommand(
                        thread_id=thread.id,
                        sender=ParticipantRef(user_type="system", user_id=0),
                        body=AGENT_DEAL_CREATED.format(lead_id=lead.id),
                    )
                )
                await self._uow.commit()
                logger.info(
                    "agent_deal_thread_created thread=%s lead_id=%s user_id=%s",
                    thread.id,
                    lead.id,
                    user_id,
                )
        else:
            logger.warning("agent_deal_thread_skipped: chat repository not wired")
        logger.info("agent_requested user_id=%s listing_id=%s", user_id, listing_id)
        return AgentRequestView(
            lead=lead,
            thread_id=thread_id,
            thread_created=thread_created,
        )


class RequestContact:
    """Заявка «Связаться с партнёром/менеджером» по объявлению (любой тариф).

    Лид `source="web"` уходит в пул партнёров (CPL-монетизация). От
    тарифа не зависит: кнопка доступна всем, но не гостям — контакт
    требует реального аккаунта. Rate-гейт: не чаще одной заявки на
    объявление за `CONTACT_WINDOW_HOURS` (sent_log kind='contact').
    """

    def __init__(
        self,
        users: UserLookupPort,
        gate: ContactRateGate,
        create_lead: CreateLead,
    ) -> None:
        self._users = users
        self._gate = gate
        self._create_lead = create_lead

    async def execute(self, user_id: int, listing_id: int, comment: str | None = None):
        """Создать заявку на контакт; возвращает созданный лид.

        Raises:
            NotFound: Пользователя нет.
            Forbidden: Гостевой аккаунт (заявка после входа).
            Conflict: Заявка по этому объявлению уже отправлена сегодня.
        """
        from app.application.partners.dto import CreateLeadCommand

        profile = await self._users.get(user_id)
        if profile is None:
            raise NotFound("User not found")
        if profile.is_guest:
            raise Forbidden(CONTACT_GUEST_FORBIDDEN)
        if await self._gate.recently_sent(user_id, listing_id):
            logger.info(
                "contact_duplicate user_id=%s listing_id=%s", user_id, listing_id
            )
            raise Conflict(CONTACT_DUPLICATE)

        # Пользовательский комментарий добавляется к стандартной формулировке:
        # партнёр видит и повод, и уточнение (пустой вариант — только повод).
        note = (
            f"{CONTACT_DEFAULT_COMMENT}. {comment.strip()}"
            if comment and comment.strip()
            else CONTACT_DEFAULT_COMMENT
        )
        # Ключ идемпотентности — на календарный день: ретраи того же запроса
        # не плодят лиды, а после суток (смена даты/окна гейта) создаётся новый.
        lead = await self._create_lead.execute(
            CreateLeadCommand(
                user_id=user_id,
                listing_id=listing_id,
                source="web",
                comment=note,
                idempotency_key=f"contact:{user_id}:{listing_id}:{date.today():%Y%m%d}",
            )
        )
        # Гейт отмечаем только после успешного создания лида: провал валидации
        # (например, 404 по объявлению) не должен блокировать заявку на сутки.
        await self._gate.mark_sent(user_id, listing_id)
        logger.info("contact_requested user_id=%s listing_id=%s", user_id, listing_id)
        return lead
