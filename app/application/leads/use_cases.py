"""Сценарии лидов: создание с идемпотентностью и частичное обновление.

Ни SQL, ни веб-фреймворка: только порт `LeadRepository` и `TrackEvent`. Правила,
которые раньше жили в роутере, перенесены сюда целиком:

- FK-валидация (`user_id`/`listing_id`) — до вставки, с 404-семантикой;
- `Idempotency-Key` — повтор возвращает тот же лид, а не дубль;
- PATCH меняет только переданные поля и не пишет no-op;
- событие воронки `lead_created` — best-effort: его падение не ломает
  создание лида (`TrackEvent` глотает ошибку сам).
"""

from __future__ import annotations

import logging

from app.application.analytics.use_cases import TrackEvent
from app.application.errors import InvalidInput, NotFound
from app.application.partners.dto import CreateLeadCommand, LeadPatch, LeadView
from app.application.partners.ports import LeadRepository
from app.domain.analytics.events import EVENT_LEAD_CREATED

logger = logging.getLogger(__name__)


class CreateLead:
    """Создать лид, не плодя дублей при ретраях бота."""

    def __init__(self, leads: LeadRepository, track: TrackEvent) -> None:
        self._leads = leads
        self._track = track

    async def execute(self, command: CreateLeadCommand) -> LeadView:
        """Создать или вернуть уже созданный лид.

        Args:
            command: Данные лида и опциональный ключ идемпотентности.

        Returns:
            Лид в объёме ответа API.

        Raises:
            NotFound: Пользователя или объявления нет.
        """
        if not await self._leads.user_exists(command.user_id):
            raise NotFound(f"User {command.user_id} not found")
        if command.listing_id is not None and not await self._leads.listing_exists(
            command.listing_id
        ):
            raise NotFound(f"Listing {command.listing_id} not found")

        if command.idempotency_key:
            existing = await self._leads.find_by_idempotency_key(
                command.idempotency_key
            )
            if existing is not None:
                logger.info(
                    "lead_idempotent_replay lead_id=%s key=%s",
                    existing.id, command.idempotency_key,
                )
                return existing

        lead = await self._leads.create(
            user_id=command.user_id,
            listing_id=command.listing_id,
            source=command.source,
            comment=command.comment,
            idempotency_key=command.idempotency_key,
        )
        # Best-effort: сбой аналитики не должен ронять ответ пользователю.
        await self._track.execute(
            command.user_id,
            EVENT_LEAD_CREATED,
            {
                "listing_id": command.listing_id,
                "source": command.source,
                "lead_id": lead.id,
            },
        )
        logger.info(
            "lead_created lead_id=%s user_id=%s listing_id=%s source=%s",
            lead.id, lead.user_id, lead.listing_id, lead.source,
        )
        return lead


class UpdateLead:
    """Сменить статус/менеджера/комментарий лида (частично)."""

    def __init__(self, leads: LeadRepository) -> None:
        self._leads = leads

    async def execute(self, lead_id: int, changes: LeadPatch) -> LeadView:
        """Применить переданные поля.

        Args:
            lead_id: ID лида.
            changes: Патч; `None`-поля не трогаются.

        Returns:
            Обновлённый лид.

        Raises:
            NotFound: Лида нет.
            InvalidInput: `manager_id` не существует.
        """
        # Менеджер проверяется до блокировки лида: не держим `FOR UPDATE`
        # на время лишнего чтения.
        if changes.manager_id is not None and not await self._leads.user_exists(
            changes.manager_id
        ):
            raise InvalidInput(f"Manager {changes.manager_id} not found")

        lead = await self._leads.patch(lead_id, changes)
        if lead is None:
            raise NotFound("Lead not found")

        logger.info(
            "lead_updated lead_id=%s new_status=%s manager_id=%s",
            lead.id, lead.status, lead.manager_id,
        )
        return lead
