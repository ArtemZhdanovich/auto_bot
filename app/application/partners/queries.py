"""Партнёрские read-модели и admin-сценарии: тонкий транспорт.

Слой не знает про SQLAlchemy и FastAPI: только порты хранилища, сквозные
порты (`EventTracker`, `AuditLog`) и чистые правила
`app.domain.partners.pricing`. Реализация портов — в
`infrastructure/services/partners/repository.py` (`SqlAlchemyPartnerRepository`).

Гарантии:
- `ListPartnerLeads` с `mine=True` фильтрует по `partner_id`: чужой лид не
  попадает ни в пул (там его нет по определению), ни в «мои» — IDOR;
- `SetLeadFeedback` меняет только лид своего партнёра, чужой → `None`
  (транспорт отдаёт 404, не раскрывая существование лида);
- admin-сценарии пишут audit через порт; политика «audit не ломает поток»
  живёт в `app.application.audit.use_cases.LogAdminAction`;
- деньги не считаются в транспорте: `PartnerOverview.cpl_cost` и
  `PlanView.cpl_cost` приходят из `app.domain.partners.pricing`.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Protocol

from app.application.errors import Conflict, InvalidInput, NotFound
from app.application.partners.use_cases import TopUp
from app.application.shared.ports import AuditLog, EventTracker
from app.domain.analytics.events import EVENT_LEAD_FEEDBACK
from app.domain.partners import pricing
from app.domain.partners.ports import PartnerAccountState


@dataclass(frozen=True, slots=True)
class LeadView:
    """Лид для партнёрского API (пул и «мои»)."""

    id: int
    source: str | None
    status: str
    comment: str | None
    qualification: dict[str, Any] | None
    created_at: datetime
    partner_id: int | None = None
    partner_status: str | None = None
    claimed_at: datetime | None = None
    user_id: int | None = None


@dataclass(frozen=True, slots=True)
class PartnerAdminView:
    """Счёт партнёра для admin-выдачи (с префиксом ключа и расходом квоты)."""

    id: int
    name: str
    contact: str | None
    plan: str
    daily_quota: int
    status: str
    balance: Decimal
    cpl_rate: Decimal | None
    created_at: datetime
    claimed_today: int = 0
    key_prefix: str | None = None


@dataclass(frozen=True, slots=True)
class PartnerOverview:
    """Состояние счёта для ЛК партнёра (`/me` и `/billing`)."""

    id: int
    name: str
    plan: str
    daily_quota: int
    claimed_today: int
    quota_remaining: int
    balance: Decimal
    cpl_cost: Decimal
    key_prefix: str | None = None


@dataclass(frozen=True, slots=True)
class PlanView:
    """Тариф каталога: квота по умолчанию и цена лида."""

    key: str
    title: str
    daily_quota: int
    cpl_cost: Decimal
    description: str | None = None


@dataclass(frozen=True, slots=True)
class BillingView:
    """Биллинг партнёра: счёт + каталог тарифов."""

    overview: PartnerOverview
    plans: tuple[PlanView, ...]


@dataclass(frozen=True, slots=True)
class IssuedKey:
    """Выданный API-ключ: raw показывается один раз, в БД — только хэш."""

    partner_id: int
    raw_key: str
    prefix: str


@dataclass(frozen=True, slots=True)
class PartnerAuthState:
    """Снимок успешной аутентификации по `X-API-Key`.

    Транспорт получает только идентификаторы — ORM-объекты ключа и
    аккаунта не покидают инфраструктуру.
    """

    key_id: int
    partner_id: int
    partner_name: str


class PartnerQueryRepository(Protocol):
    """Чтение партнёрского контура (пул, «мои» лиды, фидбек)."""

    async def get_account(self, partner_id: int) -> PartnerAccountState | None: ...

    async def claimed_today(self, partner_id: int) -> int: ...

    async def pool_leads(self, *, limit: int, offset: int) -> list[LeadView]: ...

    async def partner_leads(
        self, partner_id: int, *, limit: int, offset: int
    ) -> list[LeadView]: ...

    async def leads_by_ids(
        self, lead_ids: Sequence[int]
    ) -> list[LeadView]: ...

    async def set_feedback(
        self, partner_id: int, lead_id: int, status: str
    ) -> LeadView | None: ...

    async def find_active_api_key(self, raw_key: str) -> PartnerAuthState | None:
        """Аутентификация по сырому ключу: хэш → активный ключ + активный счёт.

        Правило: и `PartnerApiKey.status == "active"`, и `PartnerAccount.status
        == "active"`. Иначе `None` (причина — в логе репозитория): `revoke`
        без `suspend` + ротация ключа не должны давать рабочего партнёра.
        """


class PartnerAdminRepository(Protocol):
    """Admin-часть: аккаунты и ключи."""

    async def create_account(
        self,
        *,
        name: str,
        contact: str | None,
        plan: str,
        daily_quota: int,
        balance: Decimal,
        cpl_rate: Decimal | None,
    ) -> IssuedKey: ...

    async def rotate_key(self, partner_id: int) -> IssuedKey: ...

    async def revoke_keys(self, partner_id: int) -> None: ...

    async def update_account(
        self, partner_id: int, changes: dict[str, Any]
    ) -> None: ...

    async def get_admin_view(self, partner_id: int) -> PartnerAdminView | None: ...

    async def list_accounts(self) -> list[PartnerAdminView]: ...


async def _audit(
    log: AuditLog | None,
    action: str,
    partner_id: int,
    payload: dict[str, Any],
) -> None:
    """Записать admin-действие; без порта — no-op."""
    if log is None:
        return
    await log(
        action=action,
        entity="partner",
        entity_id=str(partner_id),
        payload=payload,
    )


async def _require_partner(
    repository: PartnerAdminRepository, partner_id: int
) -> PartnerAdminView:
    """Счёт партнёра или `NotFound` (единый 404 админ-ручек)."""
    view = await repository.get_admin_view(partner_id)
    if view is None:
        raise NotFound("Partner not found")
    return view


class AuthenticateApiKey:
    """Проверка `X-API-Key`: активный ключ + активный счёт.

    Единый отказ: «ключ не найден» / «ключ отозван» / «партнёр suspended»
    неразличимы для клиента — `None` → транспорт отдаёт 401, не раскрывая
    причину. Детали — в логе репозитория (audit trail).
    """

    def __init__(self, repository: PartnerQueryRepository) -> None:
        self._repository = repository

    async def execute(self, raw_key: str) -> PartnerAuthState | None:
        """Снимок аутентификации или `None` (отказ, причина — в логах)."""
        return await self._repository.find_active_api_key(raw_key)


class GetPartnerOverview:
    """Счёт партнёра: использованная квота, остаток, баланс, цена лида."""

    def __init__(self, repository: PartnerQueryRepository) -> None:
        self._repository = repository

    async def execute(self, partner_id: int) -> PartnerOverview:
        """Собрать состояние счёта.

        Raises:
            NotFound: Партнёра нет.
        """
        account = await self._repository.get_account(partner_id)
        if account is None:
            raise NotFound("Partner not found")
        used = await self._repository.claimed_today(partner_id)
        return PartnerOverview(
            id=account.id,
            name=account.name,
            plan=account.plan,
            daily_quota=account.daily_quota or 0,
            claimed_today=used,
            quota_remaining=max((account.daily_quota or 0) - used, 0),
            balance=account.balance,
            cpl_cost=pricing.cpl_cost(account.plan, account.cpl_rate),
        )


class GetPartnerBilling:
    """Биллинг партнёра: счёт + каталог тарифов."""

    def __init__(self, repository: PartnerQueryRepository) -> None:
        self._repository = repository

    async def execute(self, partner_id: int) -> BillingView:
        """Собрать биллинг.

        Raises:
            NotFound: Партнёра нет.
        """
        overview = await GetPartnerOverview(self._repository).execute(partner_id)
        plans = tuple(
            PlanView(
                key=key,
                title=spec["title"],
                daily_quota=spec["daily_quota"],
                cpl_cost=spec["cpl_cost"],
                description=spec.get("description"),
            )
            for key, spec in pricing.PLAN_CATALOG.items()
        )
        return BillingView(overview=overview, plans=plans)


class ListPartnerLeads:
    """Пул свободных лидов или только лиды своего партнёра."""

    def __init__(self, repository: PartnerQueryRepository) -> None:
        self._repository = repository

    async def execute(
        self, partner_id: int, *, mine: bool, limit: int, offset: int
    ) -> list[LeadView]:
        """`mine=True` — строго свои лиды (фильтр по `partner_id`)."""
        if mine:
            return await self._repository.partner_leads(
                partner_id, limit=limit, offset=offset
            )
        return await self._repository.pool_leads(limit=limit, offset=offset)


class ReadLeads:
    """Прочитать лиды по id после claim/batch (для ответа транспорта)."""

    def __init__(self, repository: PartnerQueryRepository) -> None:
        self._repository = repository

    async def execute(self, lead_ids: Sequence[int]) -> list[LeadView]:
        """Вернуть найденные лиды в порядке `lead_ids`."""
        if not lead_ids:
            return []
        return await self._repository.leads_by_ids(lead_ids)


class SetLeadFeedback:
    """Фидбек партнёра по качеству лида."""

    def __init__(
        self,
        repository: PartnerQueryRepository,
        events: EventTracker | None = None,
    ) -> None:
        self._repository = repository
        self._events = events

    async def execute(
        self, partner_id: int, lead_id: int, status: str
    ) -> LeadView | None:
        """Обновить `partner_status` своего лида; чужой/отсутствующий → `None`."""
        lead = await self._repository.set_feedback(partner_id, lead_id, status)
        if lead is None:
            return None
        if self._events is not None:
            await self._events(
                lead.user_id,
                EVENT_LEAD_FEEDBACK,
                {
                    "lead_id": lead.id,
                    "partner_id": partner_id,
                    "feedback": status,
                },
            )
        return lead


class ListPartners:
    """Список партнёров с расходом квоты и активным префиксом ключа."""

    def __init__(self, repository: PartnerAdminRepository) -> None:
        self._repository = repository

    async def execute(self) -> list[PartnerAdminView]:
        """O(1) запросов к БД: аккаунты + агрегаты (см. репозиторий)."""
        return await self._repository.list_accounts()


class CreatePartner:
    """Создать партнёра и выдать первый API-ключ."""

    def __init__(
        self,
        repository: PartnerAdminRepository,
        audit: AuditLog | None = None,
    ) -> None:
        self._repository = repository
        self._audit = audit

    async def execute(
        self,
        *,
        name: str,
        contact: str | None,
        plan: str,
        daily_quota: int,
        balance: Decimal,
        cpl_rate: Decimal | None,
    ) -> IssuedKey:
        """Создать счёт и ключ.

        Raises:
            InvalidInput: Неизвестный тариф.
        """
        if plan not in pricing.PLAN_CATALOG:
            raise InvalidInput(f"Unknown plan: {plan}")
        issued = await self._repository.create_account(
            name=name,
            contact=contact,
            plan=plan,
            daily_quota=daily_quota,
            balance=balance,
            cpl_rate=cpl_rate,
        )
        await _audit(
            self._audit,
            "partner.create",
            issued.partner_id,
            {"name": name, "plan": plan, "daily_quota": daily_quota},
        )
        return issued


class RotatePartnerKey:
    """Ротировать ключ: активные отзываются, выдаётся новый."""

    def __init__(
        self,
        repository: PartnerAdminRepository,
        audit: AuditLog | None = None,
    ) -> None:
        self._repository = repository
        self._audit = audit

    async def execute(self, partner_id: int) -> IssuedKey:
        """Выдать новый ключ.

        Raises:
            NotFound: Партнёра нет.
            Conflict: Партнёр не `active` (сначала активировать).
        """
        view = await _require_partner(self._repository, partner_id)
        if view.status != "active":
            raise Conflict(
                f"Partner is {view.status}; activate before rotating keys"
            )
        issued = await self._repository.rotate_key(partner_id)
        await _audit(
            self._audit,
            "partner.rotate_key",
            partner_id,
            {"key_prefix": issued.prefix},
        )
        return issued


class RevokePartnerKeys:
    """Отозвать все ключи партнёра (идемпотентно)."""

    def __init__(
        self,
        repository: PartnerAdminRepository,
        audit: AuditLog | None = None,
    ) -> None:
        self._repository = repository
        self._audit = audit

    async def execute(self, partner_id: int) -> PartnerAdminView:
        """Отозвать ключи и вернуть счёт.

        Raises:
            NotFound: Партнёра нет.
        """
        await _require_partner(self._repository, partner_id)
        await self._repository.revoke_keys(partner_id)
        view = await _require_partner(self._repository, partner_id)
        await _audit(
            self._audit,
            "partner.revoke_keys",
            partner_id,
            {"status": view.status},
        )
        return view


class TopUpPartner:
    """Пополнить/скорректировать баланс партнёра."""

    def __init__(
        self, topup: TopUp, repository: PartnerAdminRepository
    ) -> None:
        self._topup = topup
        self._repository = repository

    async def execute(
        self, partner_id: int, amount: Decimal, *, comment: str | None = None
    ) -> PartnerAdminView:
        """`amount > 0` — пополнение (реактивирует), иначе — корректировка.

        Raises:
            NotFound: Партнёра нет.
            ValueError: Нулевая сумма.
        """
        kind = "topup" if amount > 0 else "adjustment"
        await self._topup.execute(partner_id, amount, comment=comment, kind=kind)
        return await _require_partner(self._repository, partner_id)


class UpdatePartner:
    """Обновить план/квоту/статус/индивидуальный CPL."""

    def __init__(
        self,
        repository: PartnerAdminRepository,
        audit: AuditLog | None = None,
    ) -> None:
        self._repository = repository
        self._audit = audit

    async def execute(
        self,
        partner_id: int,
        *,
        plan: str | None = None,
        daily_quota: int | None = None,
        status: str | None = None,
        cpl_rate: Decimal | None = None,
        reset_cpl_rate: bool = False,
    ) -> PartnerAdminView:
        """Записать только фактически изменённые поля.

        Raises:
            NotFound: Партнёра нет.
        """
        view = await _require_partner(self._repository, partner_id)
        changes: dict[str, Any] = {}
        if plan is not None and plan != view.plan:
            changes["plan"] = plan
        if daily_quota is not None and daily_quota != view.daily_quota:
            changes["daily_quota"] = daily_quota
        if status is not None and status != view.status:
            changes["status"] = status
        if reset_cpl_rate:
            changes["cpl_rate"] = None
        elif cpl_rate is not None and cpl_rate != view.cpl_rate:
            changes["cpl_rate"] = cpl_rate

        if changes:
            await self._repository.update_account(partner_id, changes)
            await _audit(
                self._audit,
                "partner.update",
                partner_id,
                {
                    key: str(value) if isinstance(value, Decimal) else value
                    for key, value in changes.items()
                },
            )
        return await _require_partner(self._repository, partner_id)


class PartnerRateLimiter(Protocol):
    """Окно запросов партнёра (фиксированное окно; Redis down — fail-open).

    Лимит и окно — конфигурация реализации (`Settings`); транспорт знает
    только `bool` и сам маппит отказ в 429.
    """

    async def check(self, partner_id: int) -> bool:
        """`True` — запрос в лимите; `False` — превышение (→ 429)."""
