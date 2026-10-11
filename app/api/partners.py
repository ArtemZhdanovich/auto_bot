"""Партнёрский контур (P2-11): admin-управление и партнёрский API (X-API-Key).

Admin (X-Internal-Token): создание партнёра, ключи (ротация/отзыв), статистика.
Partner (X-API-Key): /me (ЛК), пул лидов, эксклюзивный claim, фидбек качества.
Фидбек — трекинг для доказательства целевости лида (accepted/deal vs junk).
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.deps import SessionDep
from app.core.deps_internal import check_internal_token
from app.core.deps_partner import (
    PartnerAuthDep,
    PartnerSessionDep,
    RedisDep,
    check_rate_limit,
)
from app.db.models import PartnerAccount
from app.schemas.partner import (
    BatchClaimIn,
    BatchClaimOut,
    BillingOut,
    BillingTopupIn,
    ClaimOut,
    FeedbackIn,
    PartnerCreateIn,
    PartnerKeyOut,
    PartnerLeadOut,
    PartnerMeOut,
    PartnerOut,
    PartnerUpdateIn,
    PartnerWithStatsOut,
    PlanInfo,
)
from app.services import partners
from app.services.admin_audit import log_admin_action
from app.services.analytics import EVENT_LEAD_FEEDBACK, EVENT_LEAD_SOLD, track

# --- Admin: /api/admin/partners (X-Internal-Token) ---------------------------

admin_router = APIRouter(
    prefix="/api/admin/partners",
    tags=["partners-admin"],
    dependencies=[Depends(check_internal_token)],
)


@admin_router.post("", response_model=PartnerKeyOut, status_code=status.HTTP_201_CREATED)
async def create_partner(body: PartnerCreateIn, session: SessionDep) -> PartnerKeyOut:
    account, raw_key = await partners.create_partner(
        session,
        name=body.name,
        contact=body.contact,
        plan=body.plan,
        daily_quota=body.daily_quota,
        balance=body.balance,
        cpl_rate=body.cpl_rate,
    )
    await log_admin_action(
        session,
        action="partner.create",
        entity="partner",
        entity_id=str(account.id),
        payload={"name": account.name, "plan": account.plan, "daily_quota": account.daily_quota},
    )
    return PartnerKeyOut(
        partner_id=account.id, key=raw_key, key_prefix=raw_key[:10] + "…"
    )


@admin_router.get("", response_model=list[PartnerWithStatsOut])
async def list_partners(session: SessionDep) -> list[PartnerWithStatsOut]:
    accounts = (await session.execute(PartnerAccount.__table__.select())).mappings().all()
    out: list[PartnerWithStatsOut] = []
    for row in accounts:
        account = await session.get(PartnerAccount, row["id"])
        out.append(
            PartnerWithStatsOut(
                id=account.id,
                name=account.name,
                contact=account.contact,
                plan=account.plan,
                daily_quota=account.daily_quota,
                status=account.status,
                balance=float(account.balance),
                cpl_rate=None
                if account.cpl_rate is None
                else float(account.cpl_rate),
                created_at=account.created_at,
                claimed_today=await partners.claimed_today(session, account.id),
                key_prefix=None,
            )
        )
    return out


async def _get_account(session: SessionDep, partner_id: int) -> PartnerAccount:
    account = await session.get(PartnerAccount, partner_id)
    if account is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Partner not found")
    return account


@admin_router.post("/{partner_id}/rotate", response_model=PartnerKeyOut)
async def rotate_partner_key(partner_id: int, session: SessionDep) -> PartnerKeyOut:
    account = await _get_account(session, partner_id)
    raw_key = await partners.rotate_key(session, account)
    await log_admin_action(
        session,
        action="partner.rotate_key",
        entity="partner",
        entity_id=str(account.id),
        payload={"key_prefix": raw_key[:10] + "…"},  # сам ключ в audit НЕ пишем
    )
    return PartnerKeyOut(
        partner_id=account.id, key=raw_key, key_prefix=raw_key[:10] + "…"
    )


@admin_router.post("/{partner_id}/revoke", response_model=PartnerOut)
async def revoke_partner_keys(partner_id: int, session: SessionDep) -> PartnerOut:
    account = await _get_account(session, partner_id)
    await partners.revoke_keys(session, account)
    await session.refresh(account)
    return PartnerOut.model_validate(account)


@admin_router.patch("/{partner_id}", response_model=PartnerOut)
async def update_partner(
    partner_id: int, body: PartnerUpdateIn, session: SessionDep
) -> PartnerOut:
    account = await _get_account(session, partner_id)
    if body.plan is not None:
        account.plan = body.plan
    if body.daily_quota is not None:
        account.daily_quota = body.daily_quota
    if body.status is not None:
        account.status = body.status
    if body.reset_cpl_rate:
        account.cpl_rate = None
    elif body.cpl_rate is not None:
        account.cpl_rate = body.cpl_rate
    await session.commit()
    await session.refresh(account)
    return PartnerOut.model_validate(account)


@admin_router.post("/{partner_id}/billing", response_model=PartnerOut)
async def partner_topup(
    partner_id: int, body: BillingTopupIn, session: SessionDep
) -> PartnerOut:
    """Пополнение/корректировка баланса (P2-14). >0 — topup (снимает
    suspended), <0 — adjustment. Записывается в partner_transactions."""
    account = await _get_account(session, partner_id)
    kind = "topup" if body.amount > 0 else "adjustment"
    account = await partners.topup(
        session, account, body.amount, comment=body.comment, kind=kind
    )
    await log_admin_action(
        session,
        action=f"partner.{kind}",
        entity="partner",
        entity_id=str(account.id),
        details={"amount": body.amount, "balance": account.balance},
    )
    return PartnerOut.model_validate(account)


# --- Partner API: /api/partner (X-API-Key) -----------------------------------

router = APIRouter(prefix="/api/partner", tags=["partners"])


@router.get("/me", response_model=PartnerMeOut)
async def partner_me(auth: PartnerAuthDep, session: PartnerSessionDep) -> PartnerMeOut:
    key, account = auth
    used = await partners.claimed_today(session, account.id)
    return PartnerMeOut(
        id=account.id,
        name=account.name,
        plan=account.plan,
        daily_quota=account.daily_quota,
        claimed_today=used,
        quota_remaining=max(account.daily_quota - used, 0),
        balance=float(account.balance),
        cpl_cost=partners.cpl_cost(account),
        key_prefix=key.key_prefix,
    )


@router.get("/leads", response_model=list[PartnerLeadOut])
async def partner_leads(
    auth: PartnerAuthDep,
    session: PartnerSessionDep,
    redis: RedisDep,
    mine: bool = Query(default=False),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> list[PartnerLeadOut]:
    _, account = auth
    await check_rate_limit(redis, account.id)
    if mine:
        # «Мои лиды» (UI/UX ТЗ §4.5): ПД доступны — лид захвачен эксклюзивно
        leads = await partners.my_leads(session, account.id, limit=limit, offset=offset)
        return [PartnerLeadOut.model_validate(lead) for lead in leads]
    leads = await partners.available_leads(session, limit=limit, offset=offset)
    # ПД (user_id) в пуле не отдаём — только после claim
    return [
        PartnerLeadOut(
            id=lead.id,
            source=lead.source,
            status=lead.status,
            comment=lead.comment,
            qualification=lead.qualification,
            created_at=lead.created_at,
        )
        for lead in leads
    ]


@router.post("/leads/batch", response_model=BatchClaimOut)
async def claim_leads_batch(
    body: BatchClaimIn,
    auth: PartnerAuthDep,
    session: PartnerSessionDep,
    redis: RedisDep,
) -> BatchClaimOut:
    """Забрать до N доступных лидов одной операцией (кнопка «Взять 3» в ЛК)."""
    _, account = auth
    await check_rate_limit(redis, account.id)
    leads, reason = await partners.claim_batch(session, account, body.limit)
    if reason == "quota_exceeded":
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Daily quota exceeded")
    if reason == "insufficient_balance":
        raise HTTPException(
            status.HTTP_402_PAYMENT_REQUIRED,
            "Insufficient balance: top up to claim leads",
        )
    for lead in leads:
        await track(
            session,
            lead.user_id,
            EVENT_LEAD_SOLD,
            {"lead_id": lead.id, "partner_id": account.id, "plan": account.plan,
             "batch": True},
        )
    return BatchClaimOut(
        claimed=[
            ClaimOut(
                lead_id=lead.id,
                partner_id=account.id,
                partner_status=lead.partner_status,
                user_id=lead.user_id,
                qualification=lead.qualification,
                claimed_at=lead.claimed_at,
            )
            for lead in leads
        ]
    )


@router.get("/billing", response_model=BillingOut)
async def partner_billing(
    auth: PartnerAuthDep,
    session: PartnerSessionDep,
    redis: RedisDep,
) -> BillingOut:
    """Текущий план, расход квоты и каталог тарифов (ЛК «Биллинг», ТЗ §4.2)."""
    _, account = auth
    await check_rate_limit(redis, account.id)
    used = await partners.claimed_today(session, account.id)
    return BillingOut(
        plan=account.plan,
        daily_quota=account.daily_quota,
        claimed_today=used,
        quota_remaining=max(account.daily_quota - used, 0),
        balance=float(account.balance),
        cpl_cost=partners.cpl_cost(account),
        plans=[
            PlanInfo(key=key, **data) for key, data in partners.PLAN_CATALOG.items()
        ],
    )


@router.post("/leads/{lead_id}/claim", response_model=ClaimOut)
async def claim_lead(
    lead_id: int,
    auth: PartnerAuthDep,
    session: PartnerSessionDep,
    redis: RedisDep,
) -> ClaimOut:
    _, account = auth
    await check_rate_limit(redis, account.id)
    lead, reason = await partners.claim_lead(session, account, lead_id)
    if reason == "taken":
        raise HTTPException(status.HTTP_409_CONFLICT, "Lead claim failed: taken")
    if reason == "not_found":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Lead not found")
    if reason == "quota_exceeded":
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Daily quota exceeded")
    if reason == "insufficient_balance":
        raise HTTPException(
            status.HTTP_402_PAYMENT_REQUIRED,
            "Insufficient balance: top up to claim leads",
        )
    assert lead is not None  # ok | already_yours
    await track(
        session,
        lead.user_id,
        EVENT_LEAD_SOLD,
        {"lead_id": lead.id, "partner_id": account.id, "plan": account.plan},
    )
    return ClaimOut(
        lead_id=lead.id,
        partner_id=account.id,
        partner_status=lead.partner_status,
        user_id=lead.user_id,
        qualification=lead.qualification,
        claimed_at=lead.claimed_at,
    )


@router.post("/leads/{lead_id}/feedback", response_model=ClaimOut)
async def lead_feedback(
    lead_id: int,
    body: FeedbackIn,
    auth: PartnerAuthDep,
    session: PartnerSessionDep,
    redis: RedisDep,
) -> ClaimOut:
    _, account = auth
    await check_rate_limit(redis, account.id)
    lead = await partners.set_feedback(session, account, lead_id, body.status)
    if lead is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "Lead not found or not claimed by you"
        )
    await track(
        session,
        lead.user_id,
        EVENT_LEAD_FEEDBACK,
        {"lead_id": lead.id, "partner_id": account.id, "feedback": body.status},
    )
    return ClaimOut(
        lead_id=lead.id,
        partner_id=account.id,
        partner_status=lead.partner_status,
        user_id=lead.user_id,
        qualification=lead.qualification,
        claimed_at=lead.claimed_at,
    )