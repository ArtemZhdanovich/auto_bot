"""Партнёрский контур (P2-11): ключи, квоты, эксклюзивная выдача лидов.

CPL-монетизация: квалифицированные лиды продаются партнёрам (ИП/перекупы/
салоны). Ключ API хранится только как SHA-256 хэш; сырой ключ показывается
один раз при создании/ротации. Ротация мгновенно инвалидует старый ключ.
"""

import hashlib
import logging
import secrets
from datetime import UTC, datetime, time

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.models import AdminAction, Lead, PartnerAccount, PartnerApiKey, PartnerTransaction
from app.services.alerting import send_alert

logger = logging.getLogger(__name__)

KEY_PREFIX = "pi_"


def hash_key(raw_key: str) -> str:
    """SHA-256 сырого ключа (без соли: ключ 160 бит энтропии, lookup по хэшу)."""
    return hashlib.sha256(raw_key.encode()).hexdigest()


def generate_api_key() -> str:
    """Сырой ключ: pi_<40 hex> — показывается один раз."""
    return KEY_PREFIX + secrets.token_hex(20)


def _today_start_utc() -> datetime:
    now = datetime.now(UTC)
    return datetime.combine(now.date(), time.min, tzinfo=UTC)


async def create_partner(
    session: AsyncSession,
    name: str,
    contact: str | None = None,
    plan: str = "starter",
    daily_quota: int = 10,
    balance: float = 0.0,
    cpl_rate: float | None = None,
) -> tuple[PartnerAccount, str]:
    """Создать партнёра и активный ключ. Возвращает (аккаунт, сырой_ключ)."""
    account = PartnerAccount(
        name=name,
        contact=contact,
        plan=plan,
        daily_quota=daily_quota,
        balance=balance,
        cpl_rate=cpl_rate,
        status="active",
    )
    session.add(account)
    await session.flush()
    raw_key = await issue_key(session, account)
    await session.commit()
    await session.refresh(account)
    return account, raw_key


async def issue_key(session: AsyncSession, account: PartnerAccount) -> str:
    """Выдать новый активный ключ партнёру (без commit — на вызывающем)."""
    raw_key = generate_api_key()
    session.add(
        PartnerApiKey(
            partner_id=account.id,
            key_hash=hash_key(raw_key),
            key_prefix=raw_key[:10] + "…",
            status="active",
        )
    )
    return raw_key


async def rotate_key(session: AsyncSession, account: PartnerAccount) -> str:
    """Ротация: старые ключи мгновенно rotated, выдаётся новый сырой ключ."""
    result = await session.execute(
        select(PartnerApiKey).where(
            PartnerApiKey.partner_id == account.id,
            PartnerApiKey.status == "active",
        )
    )
    now = datetime.now(UTC)
    for key in result.scalars().all():
        key.status = "rotated"
        key.rotated_at = now
    raw_key = await issue_key(session, account)
    await session.commit()
    return raw_key


async def revoke_keys(session: AsyncSession, account: PartnerAccount) -> int:
    """Отозвать все активные ключи (доступ закрывается без удаления аккаунта)."""
    result = await session.execute(
        select(PartnerApiKey).where(
            PartnerApiKey.partner_id == account.id,
            PartnerApiKey.status == "active",
        )
    )
    now = datetime.now(UTC)
    count = 0
    for key in result.scalars().all():
        key.status = "revoked"
        key.rotated_at = now
        count += 1
    await session.commit()
    return count


async def authenticate_key(
    session: AsyncSession, raw_key: str
) -> tuple[PartnerApiKey, PartnerAccount] | None:
    """Аутентификация по X-API-Key: активный ключ + активный аккаунт."""
    result = await session.execute(
        select(PartnerApiKey).where(PartnerApiKey.key_hash == hash_key(raw_key))
    )
    key = result.scalar_one_or_none()
    if key is None or key.status != "active":
        return None
    account = await session.get(PartnerAccount, key.partner_id)
    if account is None or account.status != "active":
        return None
    key.last_used_at = datetime.now(UTC)
    await session.commit()
    return key, account


async def claimed_today(session: AsyncSession, partner_id: int) -> int:
    """Сколько лидов партнёр забрал за сегодня (UTC) — для квоты и ЛК."""
    result = await session.execute(
        select(func.count())
        .select_from(Lead)
        .where(
            Lead.partner_id == partner_id,
            Lead.claimed_at >= _today_start_utc(),
        )
    )
    return int(result.scalar_one())


async def claim_lead(
    session: AsyncSession, account: PartnerAccount, lead_id: int
) -> tuple[Lead | None, str]:
    """Эксклюзивный claim лида. Возвращает (лид|None, причина).

    Причины: ok / not_found / taken / already_yours / quota_exceeded /
    insufficient_balance (биллинг P2-14: CPL-списание с баланса).
    """
    used = await claimed_today(session, account.id)
    if used >= account.daily_quota:
        return None, "quota_exceeded"

    cost = cpl_cost(account)
    if account.balance < cost:
        await _suspend_for_empty_balance(session, account)
        return None, "insufficient_balance"

    lead = await session.get(Lead, lead_id)
    if lead is None:
        return None, "not_found"
    if lead.partner_id == account.id:
        return lead, "already_yours"  # идемпотентный повтор
    if lead.partner_id is not None or lead.status != "new":
        return None, "taken"

    lead.partner_id = account.id
    lead.partner_status = "claimed"
    lead.claimed_at = datetime.now(UTC)
    lead.updated_at = lead.claimed_at
    await _charge(session, account, cost, lead_id=lead.id)
    await session.commit()
    await session.refresh(lead)
    return lead, "ok"


PARTNER_FEEDBACK_STATUSES = ("claimed", "accepted", "returned", "junk", "deal")


async def set_feedback(
    session: AsyncSession, account: PartnerAccount, lead_id: int, status: str
) -> Lead | None:
    """Фидбек партнёра по качеству лида (только по своим лидам)."""
    lead = await session.get(Lead, lead_id)
    if lead is None or lead.partner_id != account.id:
        return None
    lead.partner_status = status
    lead.updated_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(lead)
    return lead


async def available_leads(
    session: AsyncSession, limit: int = 20, offset: int = 0
) -> list[Lead]:
    """Пул доступных лидов: новые, никем не забранные, с квалификацией."""
    result = await session.execute(
        select(Lead)
        .where(
            Lead.status == "new",
            Lead.partner_id.is_(None),
            Lead.qualification.is_not(None),
        )
        .order_by(Lead.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(result.scalars().all())


async def my_leads(
    session: AsyncSession, partner_id: int, limit: int = 20, offset: int = 0
) -> list[Lead]:
    """Лиды, захваченные партнёром (для ЛК «Мои лиды», UI/UX ТЗ §4.5)."""
    result = await session.execute(
        select(Lead)
        .where(Lead.partner_id == partner_id)
        .order_by(Lead.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(result.scalars().all())


async def claim_batch(
    session: AsyncSession, account: PartnerAccount, limit: int = 3
) -> tuple[list[Lead], str]:
    """Забрать до N доступных лидов одной операцией (кнопка «Взять 3» в ЛК).

    Учитывает остаток дневной квоты И баланс (CPL-списание за каждый лид):
    берём min(limit, quota_remaining, affordable).
    Возвращает (захваченные лиды, статус): ok | quota_exceeded |
    insufficient_balance.
    """
    used = await claimed_today(session, account.id)
    remaining = account.daily_quota - used
    if remaining <= 0:
        return [], "quota_exceeded"
    cost = cpl_cost(account)
    affordable = int(account.balance // cost) if cost > 0 else remaining
    if affordable <= 0:
        await _suspend_for_empty_balance(session, account)
        return [], "insufficient_balance"
    take = min(limit, remaining, affordable)
    result = await session.execute(
        select(Lead)
        .where(
            Lead.status == "new",
            Lead.partner_id.is_(None),
            Lead.qualification.is_not(None),
        )
        .order_by(Lead.id.desc())
        .limit(take)
    )
    leads = list(result.scalars().all())
    now = datetime.now(UTC)
    for lead in leads:
        lead.partner_id = account.id
        lead.partner_status = "claimed"
        lead.claimed_at = now
        lead.updated_at = now
        await _charge(session, account, cost, lead_id=lead.id)
    if leads:
        await session.commit()
        for lead in leads:
            await session.refresh(lead)
    return leads, "ok"


# --- Биллинг (P2-14): CPL-тарифы, баланс, приостановка ----------------------


def cpl_cost(account: PartnerAccount) -> float:
    """Цена лида: индивидуальный cpl_rate или тариф плана (fallback — starter)."""
    if account.cpl_rate is not None:
        return float(account.cpl_rate)
    rates = get_settings().partner_cpl_rates
    return float(rates.get(account.plan, rates.get("starter", 0.0)))


def _round2(value: float) -> float:
    return round(value + 1e-9, 2)


async def _charge(
    session: AsyncSession, account: PartnerAccount, cost: float, lead_id: int
) -> None:
    """Списать cost с баланса и записать транзакцию (commit на вызывающем)."""
    account.balance = _round2(float(account.balance) - cost)
    session.add(
        PartnerTransaction(
            partner_id=account.id,
            lead_id=lead_id,
            amount=_round2(-cost),
            kind="charge_lead",
            balance_after=account.balance,
        )
    )


async def topup(
    session: AsyncSession,
    account: PartnerAccount,
    amount: float,
    comment: str | None = None,
    kind: str = "topup",
) -> PartnerAccount:
    """Пополнение/корректировка баланса. Положительное пополнение снимает
    suspended, выставленный при исчерпании баланса."""
    account.balance = _round2(float(account.balance) + amount)
    session.add(
        PartnerTransaction(
            partner_id=account.id,
            amount=_round2(amount),
            kind=kind,
            balance_after=account.balance,
            comment=comment,
        )
    )
    if amount > 0 and account.status == "suspended":
        account.status = "active"
        await session.flush()
        session.add(
            AdminAction(
                action="partner.reactivate",
                entity="partner",
                entity_id=str(account.id),
                payload={"balance": account.balance},
            )
        )
    await session.commit()
    await session.refresh(account)
    return account


async def _suspend_for_empty_balance(
    session: AsyncSession, account: PartnerAccount
) -> None:
    """Баланс исчерпан: приостановить (однократно) и алертить админу."""
    if account.status == "suspended":
        return
    account.status = "suspended"
    await session.commit()
    await session.refresh(account)
    logger.warning(
        "partner_suspended_no_balance: partner_id=%s balance=%s",
        account.id,
        account.balance,
    )
    await send_alert(
        f"⚠️ Партнёр #{account.id} «{account.name}» приостановлен: "
        f"баланс {account.balance} < цены лида. Пополните баланс.",
        dedup_key=f"partner_suspended:{account.id}",
        dedup_ttl=86400,
    )


# --- Биллинг: каталог тарифов CPL (значения квот — дефолты при создании) -------

PLAN_CATALOG: dict[str, dict] = {
    "starter": {"title": "Starter", "daily_quota": 5, "price_usd": 49},
    "pro": {"title": "Pro", "daily_quota": 15, "price_usd": 149},
    "salon": {"title": "Salon", "daily_quota": 40, "price_usd": 399},
}


