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
    ClaimOut,
    FeedbackIn,
    PartnerCreateIn,
    PartnerKeyOut,
    PartnerLeadOut,
    PartnerMeOut,
    PartnerOut,
    PartnerUpdateIn,
    PartnerWithStatsOut,
)
from app.services import partners
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
    await session.commit()
    await session.refresh(account)
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
        key_prefix=key.key_prefix,
    )


@router.get("/leads", response_model=list[PartnerLeadOut])
async def partner_leads(
    auth: PartnerAuthDep,
    session: PartnerSessionDep,
    redis: RedisDep,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> list[PartnerLeadOut]:
    _, account = auth
    await check_rate_limit(redis, account.id)
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