"""SQL-реализация порта `PartnerRepository` на `AsyncSession`.

Отвечает только за доступ к БД (ORM → доменные снимки). Логика use case'ов
вынесена в `app.application.partners.use_cases`, фасады для легаси — в
`app.infrastructure.services.partners.facades`.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import Select, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.partners.queries import (
    IssuedKey,
    LeadView,
    PartnerAdminView,
    PartnerAuthState,
)
from app.domain.partners.ports import (
    CLAIM_ALREADY_YOURS,
    CLAIM_NOT_FOUND,
    CLAIM_OK,
    CLAIM_TAKEN,
    ClaimAttempt,
    PartnerAccountState,
    PartnerLead,
)
from app.infrastructure.db.models import (
    Lead,
    PartnerAccount,
    PartnerApiKey,
    PartnerTransaction,
)
from app.infrastructure.security.partner_keys import (
    generate_api_key,
    hash_api_key,
    key_prefix,
)

logger = logging.getLogger(__name__)


def _pool_stmt(limit: int, offset: int) -> Select[tuple[Lead]]:
    """Пул: `partner_id IS NULL`, старые первыми."""
    return (
        select(Lead)
        .where(Lead.partner_id.is_(None))
        .order_by(Lead.created_at.asc())
        .limit(limit)
        .offset(offset)
    )


def _my_leads_stmt(
    partner_id: int, limit: int, offset: int
) -> Select[tuple[Lead]]:
    """Лиды партнёра, свежие первыми."""
    return (
        select(Lead)
        .where(Lead.partner_id == partner_id)
        .order_by(Lead.claimed_at.desc().nulls_last())
        .limit(limit)
        .offset(offset)
    )


def _account_state(account: PartnerAccount) -> PartnerAccountState:
    """ORM-аккаунт → снимок для домена."""
    return PartnerAccountState(
        id=account.id,
        name=account.name,
        plan=account.plan,
        status=account.status,
        balance=account.balance,
        daily_quota=account.daily_quota,
        cpl_rate=account.cpl_rate,
    )


def _lead_state(lead: Lead) -> PartnerLead:
    """ORM-лид → снимок для домена."""
    return PartnerLead(
        id=lead.id,
        user_id=lead.user_id,
        partner_id=lead.partner_id,
        partner_status=lead.partner_status,
        qualification=lead.qualification,
        claimed_at=lead.claimed_at,
    )


def _lead_view(lead: Lead) -> LeadView:
    """ORM-лид → read-модель партнёрского API."""
    return LeadView(
        id=lead.id,
        source=lead.source,
        status=lead.status,
        comment=lead.comment,
        qualification=lead.qualification,
        created_at=lead.created_at,
        partner_id=lead.partner_id,
        partner_status=lead.partner_status,
        claimed_at=lead.claimed_at,
        user_id=lead.user_id,
    )


def _admin_view(
    account: PartnerAccount,
    *,
    claimed_today: int = 0,
    key_prefix: str | None = None,
) -> PartnerAdminView:
    """ORM-аккаунт → admin-выдача (без N+1: агрегаты приходят снаружи)."""
    return PartnerAdminView(
        id=account.id,
        name=account.name,
        contact=account.contact,
        plan=account.plan,
        daily_quota=account.daily_quota,
        status=account.status,
        balance=account.balance,
        cpl_rate=account.cpl_rate,
        created_at=account.created_at,
        claimed_today=claimed_today,
        key_prefix=key_prefix,
    )


def _today_start() -> datetime:
    """Начало текущих суток в UTC (граница суточной квоты)."""
    return datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)


# --- SQL-реализация порта --------------------------------------------


class SqlAlchemyPartnerRepository:
    """`PartnerRepository` на `AsyncSession`."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_account(self, partner_id: int) -> PartnerAccountState | None:
        account = await self._session.get(
            PartnerAccount, partner_id, populate_existing=True
        )
        return None if account is None else _account_state(account)

    async def find_active_api_key(self, raw_key: str) -> PartnerAuthState | None:
        """Ключ по хэшу + активный счёт, иначе `None` (причина — в логе).

        Правило «ключ active И аккаунт active» (см. порт): `revoke` без
        `suspend` + ротация не дают рабочего партнёра. Клиенту всегда 401
        без причины — детали только в логе (audit trail).
        """
        key_hash = hash_api_key(raw_key)
        key = (
            await self._session.execute(
                select(PartnerApiKey).where(PartnerApiKey.key_hash == key_hash)
            )
        ).scalar_one_or_none()
        if key is None or key.status != "active":
            return None
        account = await self._session.get(PartnerAccount, key.partner_id)
        if account is None or account.status != "active":
            # suspended-партнёр: клиент получит 401 (не раскрываем статус);
            # admin видит причину через audit/log.
            logger.warning(
                "partner_auth_rejected account_id=%s status=%s",
                key.partner_id,
                account.status if account else "missing",
            )
            return None
        return PartnerAuthState(
            key_id=key.id,
            partner_id=account.id,
            partner_name=account.name,
        )

    async def update_balance_returning(
        self,
        partner_id: int,
        delta: Decimal,
        *,
        status: str | None = None,
    ) -> Decimal:
        values: dict = {"balance": PartnerAccount.balance + delta}
        if status is not None:
            values["status"] = status
        return (
            await self._session.execute(
                update(PartnerAccount)
                .where(PartnerAccount.id == partner_id)
                .values(**values)
                .returning(PartnerAccount.balance)
            )
        ).scalar_one()

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
        self._session.add(
            PartnerTransaction(
                partner_id=partner_id,
                lead_id=lead_id,
                amount=amount,
                kind=kind,
                balance_after=balance_after,
                comment=comment,
            )
        )

    async def claim_lead_row(
        self, partner_id: int, lead_id: int, *, claimed_at: datetime
    ) -> ClaimAttempt:
        claimed = (
            await self._session.execute(
                update(Lead)
                .where(Lead.id == lead_id)
                .where(Lead.partner_id.is_(None))
                .values(
                    partner_id=partner_id,
                    partner_status="claimed",
                    claimed_at=claimed_at,
                )
                .returning(Lead)
            )
        ).scalars().one_or_none()

        if claimed is not None:
            return ClaimAttempt(outcome=CLAIM_OK, lead=_lead_state(claimed))

        existing = await self._session.get(Lead, lead_id, populate_existing=True)
        if existing is None:
            return ClaimAttempt(outcome=CLAIM_NOT_FOUND)
        if existing.partner_id == partner_id:
            return ClaimAttempt(
                outcome=CLAIM_ALREADY_YOURS, lead=_lead_state(existing)
            )
        return ClaimAttempt(outcome=CLAIM_TAKEN)

    async def claim_batch_rows(
        self, partner_id: int, limit: int, *, claimed_at: datetime
    ) -> tuple[PartnerLead, ...]:
        candidate_ids = (
            await self._session.execute(
                text(
                    """
                    SELECT id FROM leads
                    WHERE partner_id IS NULL
                    ORDER BY created_at ASC
                    LIMIT :lim
                    FOR UPDATE SKIP LOCKED
                    """
                ),
                {"lim": limit},
            )
        ).scalars().all()
        if not candidate_ids:
            return ()

        claimed = (
            await self._session.execute(
                update(Lead)
                .where(Lead.id.in_(candidate_ids))
                .where(Lead.partner_id.is_(None))
                .values(
                    partner_id=partner_id,
                    partner_status="claimed",
                    claimed_at=claimed_at,
                )
                .returning(Lead)
            )
        ).scalars().all()
        return tuple(_lead_state(lead) for lead in claimed)

    async def available_leads(
        self, *, limit: int, offset: int
    ) -> list[PartnerLead]:
        rows = (await self._session.execute(_pool_stmt(limit, offset))).scalars().all()
        return [_lead_state(row) for row in rows]

    async def my_leads(
        self, partner_id: int, *, limit: int, offset: int
    ) -> list[PartnerLead]:
        rows = (
            await self._session.execute(_my_leads_stmt(partner_id, limit, offset))
        ).scalars().all()
        return [_lead_state(row) for row in rows]

    async def pool_leads(self, *, limit: int, offset: int) -> list[LeadView]:
        rows = (
            await self._session.execute(_pool_stmt(limit, offset))
        ).scalars().all()
        return [_lead_view(row) for row in rows]

    async def partner_leads(
        self, partner_id: int, *, limit: int, offset: int
    ) -> list[LeadView]:
        rows = (
            await self._session.execute(
                _my_leads_stmt(partner_id, limit, offset)
            )
        ).scalars().all()
        return [_lead_view(row) for row in rows]

    async def leads_by_ids(
        self, lead_ids: Sequence[int]
    ) -> list[LeadView]:
        if not lead_ids:
            return []
        rows = (
            await self._session.execute(
                select(Lead).where(Lead.id.in_(list(lead_ids)))
            )
        ).scalars().all()
        by_id = {row.id: _lead_view(row) for row in rows}
        return [by_id[lead_id] for lead_id in lead_ids if lead_id in by_id]

    async def set_feedback(
        self, partner_id: int, lead_id: int, status: str
    ) -> LeadView | None:
        lead = (
            await self._session.execute(
                select(Lead)
                .where(Lead.id == lead_id)
                .where(Lead.partner_id == partner_id)
            )
        ).scalar_one_or_none()
        if lead is None:
            return None
        lead.partner_status = status
        await self._session.commit()
        await self._session.refresh(lead)
        return _lead_view(lead)

    async def claimed_today(self, partner_id: int) -> int:
        return (
            await self._session.execute(
                select(func.count())
                .select_from(Lead)
                .where(Lead.partner_id == partner_id)
                .where(Lead.claimed_at >= _today_start())
            )
        ).scalar_one()

    # --- Admin: аккаунты и ключи (порт `PartnerAdminRepository`) -----

    async def create_account(
        self,
        *,
        name: str,
        contact: str | None,
        plan: str,
        daily_quota: int,
        balance: Decimal,
        cpl_rate: Decimal | None,
    ) -> IssuedKey:
        account = PartnerAccount(
            name=name,
            contact=contact,
            plan=plan,
            daily_quota=daily_quota,
            status="active",
            balance=Decimal(balance),
            cpl_rate=cpl_rate,
        )
        self._session.add(account)
        await self._session.flush()
        partner_id = account.id

        raw_key = generate_api_key()
        self._session.add(
            PartnerApiKey(
                partner_id=partner_id,
                key_hash=hash_api_key(raw_key),
                key_prefix=key_prefix(raw_key),
                status="active",
            )
        )
        await self._session.commit()
        return IssuedKey(
            partner_id=partner_id,
            raw_key=raw_key,
            prefix=key_prefix(raw_key),
        )

    async def rotate_key(self, partner_id: int) -> IssuedKey:
        await self._session.execute(
            update(PartnerApiKey)
            .where(
                PartnerApiKey.partner_id == partner_id,
                PartnerApiKey.status == "active",
            )
            .values(status="rotated", rotated_at=datetime.now(UTC))
        )
        raw_key = generate_api_key()
        self._session.add(
            PartnerApiKey(
                partner_id=partner_id,
                key_hash=hash_api_key(raw_key),
                key_prefix=key_prefix(raw_key),
                status="active",
            )
        )
        await self._session.commit()
        return IssuedKey(
            partner_id=partner_id,
            raw_key=raw_key,
            prefix=key_prefix(raw_key),
        )

    async def revoke_keys(self, partner_id: int) -> None:
        await self._session.execute(
            update(PartnerApiKey)
            .where(
                PartnerApiKey.partner_id == partner_id,
                PartnerApiKey.status != "revoked",
            )
            .values(status="revoked", rotated_at=datetime.now(UTC))
        )
        await self._session.commit()

    async def update_account(
        self, partner_id: int, changes: dict
    ) -> None:
        if not changes:
            return
        await self._session.execute(
            update(PartnerAccount)
            .where(PartnerAccount.id == partner_id)
            .values(**changes)
        )
        await self._session.commit()

    async def get_admin_view(
        self, partner_id: int
    ) -> PartnerAdminView | None:
        account = await self._session.get(
            PartnerAccount, partner_id, populate_existing=True
        )
        if account is None:
            return None
        prefix = (
            await self._session.execute(
                select(PartnerApiKey.key_prefix)
                .where(
                    PartnerApiKey.partner_id == partner_id,
                    PartnerApiKey.status == "active",
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        return _admin_view(
            account,
            claimed_today=await self.claimed_today(partner_id),
            key_prefix=prefix,
        )

    async def list_accounts(self) -> list[PartnerAdminView]:
        accounts = (
            await self._session.execute(select(PartnerAccount))
        ).scalars().all()
        if not accounts:
            return []
        ids = [account.id for account in accounts]
        claimed_map = dict(
            (
                await self._session.execute(
                    select(Lead.partner_id, func.count())
                    .where(Lead.partner_id.in_(ids))
                    .where(Lead.claimed_at >= _today_start())
                    .group_by(Lead.partner_id)
                )
            ).all()
        )
        keys_map = dict(
            (
                await self._session.execute(
                    select(PartnerApiKey.partner_id, PartnerApiKey.key_prefix)
                    .where(
                        PartnerApiKey.partner_id.in_(ids),
                        PartnerApiKey.status == "active",
                    )
                )
            ).all()
        )
        return [
            _admin_view(
                account,
                claimed_today=claimed_map.get(account.id, 0),
                key_prefix=keys_map.get(account.id),
            )
            for account in accounts
        ]

    async def suspend_for_empty_balance(
        self, account: PartnerAccountState
    ) -> None:
        if account.status == "suspended":
            return
        await self._session.execute(
            update(PartnerAccount)
            .where(PartnerAccount.id == account.id)
            .values(status="suspended")
        )
        await self._session.commit()
        try:
            from app.infrastructure.services.alerting import send_alert

            await send_alert(
                f"🔴 Партнёр {account.name} (id={account.id}) приостановлен: "
                f"недостаточно баланса ({account.balance}).",
                dedup_key=f"partner_suspended:{account.id}",
            )
        except Exception:  # noqa: BLE001 — алерт не должен ломать 402
            logger.warning(
                "partner_suspend_alert_failed id=%s", account.id, exc_info=True
            )