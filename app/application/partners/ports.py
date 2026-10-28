"""Порты партнёрского контура (партнёры + лиды).

Интерфейс хранилища живёт здесь (прикладной слой), а не в домене.
Реализация — в пакете `infrastructure/services/partners` (SQL-репозиторий).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from typing import Any, Protocol

from app.application.partners.dto import (
    IssuedKey,
    LeadPatch,
    LeadView,
    PartnerAdminView,
    PartnerAuthState,
)
from app.domain.partners.ports import (
    ClaimAttempt,
    PartnerAccountState,
    PartnerLead,
)


class PartnerRepository(Protocol):
    """Хранилище партнёрского контура."""

    async def get_account(self, partner_id: int) -> PartnerAccountState | None:
        """Свежий снимок счёта или `None`, если партнёра нет."""

    async def update_balance_returning(
        self,
        partner_id: int,
        delta: Decimal,
        *,
        status: str | None = None,
    ) -> Decimal:
        """Атомарно изменить баланс и вернуть новое значение.

        `status` (если задан) пишется тем же `UPDATE`: реактивация после
        пополнения не должна требовать второго запроса.
        """

    async def create_transaction(
        self,
        *,
        partner_id: int,
        lead_id: int | None,
        amount: Decimal,
        kind: str,
        balance_after: Decimal,
        comment: str | None = None,
    ) -> None:
        """Записать движение по счёту в журнал (в текущей транзакции)."""

    async def claim_lead_row(
        self, partner_id: int, lead_id: int, *, claimed_at: datetime
    ) -> ClaimAttempt:
        """Атомарно забрать один лид; при промахе — объяснить причину."""

    async def claim_batch_rows(
        self, partner_id: int, limit: int, *, claimed_at: datetime
    ) -> tuple[PartnerLead, ...]:
        """Забрать до `limit` свободных лидов (`SKIP LOCKED`)."""

    async def available_leads(
        self, *, limit: int, offset: int
    ) -> list[PartnerLead]:
        """Пул: свободные лиды, старые первыми."""

    async def my_leads(
        self, partner_id: int, *, limit: int, offset: int
    ) -> list[PartnerLead]:
        """Лиды партнёра, свежие первыми."""

    async def claimed_today(self, partner_id: int) -> int:
        """Сколько лидов партнёр забрал с 00:00 UTC."""

    async def suspend_for_empty_balance(self, account: PartnerAccountState) -> None:
        """Приостановить партнёра из-за нулевого баланса и уведомить админа.

        Реализация коммитит изменение сама: вызывающий сразу возвращает
        отказ, а транспорт на отказе делает rollback — без commit статус
        `suspended` не сохранился бы.
        """


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
        owner_telegram_id: int | None = None,
    ) -> IssuedKey: ...

    async def rotate_key(self, partner_id: int) -> IssuedKey: ...

    async def revoke_keys(self, partner_id: int) -> None: ...

    async def update_account(
        self, partner_id: int, changes: dict[str, Any]
    ) -> None: ...

    async def get_admin_view(self, partner_id: int) -> PartnerAdminView | None: ...

    async def list_accounts(self) -> list[PartnerAdminView]: ...


class PartnerRateLimiter(Protocol):
    """Окно запросов партнёра (фиксированное окно; Redis down — fail-open).

    Лимит и окно — конфигурация реализации (`Settings`); транспорт знает
    только `bool` и сам маппит отказ в 429.
    """

    async def check(self, partner_id: int) -> bool:
        """`True` — запрос в лимите; `False` — превышение (→ 429)."""


class LeadRepository(Protocol):
    """Хранилище лидов: сценарий не знает про SQL и транзакции.

    `create` сама разрешает гонку по `idempotency_key` (partial unique):
    при конфликте возвращает уже существующую строку.
    """

    async def user_exists(self, user_id: int) -> bool:
        """Есть ли пользователь (менеджер) с таким `telegram_id`."""

    async def listing_exists(self, listing_id: int) -> bool:
        """Есть ли объявление с таким `id`."""

    async def find_by_idempotency_key(self, key: str) -> LeadView | None:
        """Лид, созданный с этим ключом идемпотентности, или `None`."""

    async def create(
        self,
        *,
        user_id: int,
        listing_id: int | None,
        source: str | None,
        comment: str | None,
        idempotency_key: str | None,
    ) -> LeadView:
        """Вставить лид (или вернуть существующий по ключу) и закоммитить."""

    async def patch(self, lead_id: int, changes: LeadPatch) -> LeadView | None:
        """Применить патч под блокировкой строки; `None` — лида нет."""

