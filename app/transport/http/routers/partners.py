"""Партнёрский контур (P2-11): тонкий транспорт.

Admin (X-Internal-Token): создание партнёра, ключи (ротация/отзыв), статистика.
Partner (X-API-Key): /me (ЛК), пул лидов, эксклюзивный claim, фидбек качества,
/market (сводка рынка + выгодные лоты).

Здесь нет SQL, сессии и денежных правил: контроллер разбирает ввод, вызывает
сценарий (`app.application.partners`) и маппит результат в схему.

Гарантии (живут в application и инфраструктуре):
- атомарный claim (`UPDATE … RETURNING`, `SKIP LOCKED`) — репозиторий;
- auto-suspend + алерт при пустом балансе — сценарий + порт;
- audit admin-действий и аналитика — сквозные порты (`AuditLog`,
  `EventTracker`), сбой не ломает бизнес-операцию;
- чужой лид не виден и не меняется: 404/409 без раскрытия существования.
"""


from __future__ import annotations
from app.transport.http.deps.dishka import DishkaRoute, InternalDishkaRoute

import logging
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query, status

from app.application.errors import Conflict, InvalidInput, NotFound
from app.application.partners.dto import PartnerLeadView
from app.transport.http.deps.partner import (
    PartnerAuthDep,
    RateLimitDep,
    enforce_rate_limit,
)
from app.transport.http.deps.use_cases import (
    ClaimBatchDep,
    ClaimLeadDep,
    CreatePartnerDep,
    ListPartnersDep,
    PartnerBillingDep,
    PartnerFeedbackDep,
    PartnerLeadsDep,
    PartnerMarketDep,
    PartnerOverviewDep,
    PartnerTopUpDep,
    ReadLeadsDep,
    RevokePartnerKeysDep,
    RotatePartnerKeyDep,
    UpdatePartnerDep,
)
from app.transport.http.schemas.partner import (
    BatchClaimIn,
    BatchClaimOut,
    BillingOut,
    BillingTopupIn,
    ClaimOut,
    FeedbackIn,
    MarketDealOut,
    MarketOut,
    PartnerCreateIn,
    PartnerKeyOut,
    PartnerLeadOwnedOut,
    PartnerLeadPublicOut,
    PartnerMeOut,
    PartnerOut,
    PartnerUpdateIn,
    PartnerWithStatsOut,
    PlanInfo,
)

logger = logging.getLogger(__name__)

PartnerIdPath = Annotated[int, Path(ge=1)]


def _raise_for_claim_reason(reason: str) -> None:
    """Причина отказа claim'а → HTTP-статус (коды сценария партнёров)."""
    if reason == "quota_exceeded":
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS, "Daily quota exceeded"
        )
    if reason == "insufficient_balance":
        raise HTTPException(
            status.HTTP_402_PAYMENT_REQUIRED,
            "Insufficient balance: top up to claim leads",
        )


def _not_found(exc: NotFound) -> HTTPException:
    """`NotFound` сценария → 404 с публичным текстом."""
    return HTTPException(status.HTTP_404_NOT_FOUND, exc.public_message)


def _claim_out(partner_id: int, lead: PartnerLeadView) -> ClaimOut:
    """Лид после claim → схема ответа (PII отдаётся только владельцу)."""
    return ClaimOut(
        lead_id=lead.id,
        partner_id=partner_id,
        partner_status=lead.partner_status,
        user_id=lead.user_id,
        qualification=lead.qualification,
        claimed_at=lead.claimed_at,
    )


# =========================================================================
# Admin: /api/admin/partners
# =========================================================================

admin_router = APIRouter(route_class=InternalDishkaRoute,
    prefix="/api/admin/partners",
    tags=["partners-admin"],
)


@admin_router.post(
    "", response_model=PartnerKeyOut, status_code=status.HTTP_201_CREATED
)
async def create_partner(
    body: PartnerCreateIn, use_case: CreatePartnerDep
) -> PartnerKeyOut:
    """Создать партнёра + первый API-ключ (raw-ключ отдаётся один раз).

    `owner_telegram_id` (если задан) — владелец счёта: пользователь
    получает бессрочный Business сразу (422, если пользователя нет).
    """
    try:
        issued = await use_case.execute(
            name=body.name,
            contact=body.contact,
            plan=body.plan,
            daily_quota=body.daily_quota,
            balance=body.balance,
            cpl_rate=body.cpl_rate,
            owner_telegram_id=body.owner_telegram_id,
        )
    except InvalidInput as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, exc.public_message
        ) from exc
    return PartnerKeyOut(
        partner_id=issued.partner_id,
        key=issued.raw_key,
        key_prefix=issued.prefix,
    )


@admin_router.get("", response_model=list[PartnerWithStatsOut])
async def list_partners(use_case: ListPartnersDep) -> list[PartnerWithStatsOut]:
    """Партнёры + claimed_today и активный key_prefix (O(1) запросов)."""
    partners = await use_case.execute()
    return [PartnerWithStatsOut.model_validate(view) for view in partners]


@admin_router.post("/{partner_id}/rotate", response_model=PartnerKeyOut)
async def rotate_partner_key(
    use_case: RotatePartnerKeyDep, partner_id: PartnerIdPath
) -> PartnerKeyOut:
    """Ротировать ключ. Для `suspended` — 409 (сначала активировать)."""
    try:
        issued = await use_case.execute(partner_id)
    except NotFound as exc:
        raise _not_found(exc) from exc
    except Conflict as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT, exc.public_message
        ) from exc
    return PartnerKeyOut(
        partner_id=issued.partner_id,
        key=issued.raw_key,
        key_prefix=issued.prefix,
    )


@admin_router.post("/{partner_id}/revoke", response_model=PartnerOut)
async def revoke_partner_keys(
    use_case: RevokePartnerKeysDep, partner_id: PartnerIdPath
) -> PartnerOut:
    """Отозвать все ключи (audit — security-событие, через порт)."""
    try:
        view = await use_case.execute(partner_id)
    except NotFound as exc:
        raise _not_found(exc) from exc
    return PartnerOut.model_validate(view)


@admin_router.patch("/{partner_id}", response_model=PartnerOut)
async def update_partner(
    body: PartnerUpdateIn,
    use_case: UpdatePartnerDep,
    partner_id: PartnerIdPath,
) -> PartnerOut:
    """Обновить plan/quota/status/cpl_rate/owner (пишутся только изменения).

    Смена владельца или статуса синхронизирует тариф владельца:
    active → Business, suspended → free (422, если владельца нет).
    """
    try:
        view = await use_case.execute(
            partner_id,
            plan=body.plan,
            daily_quota=body.daily_quota,
            status=body.status,
            cpl_rate=body.cpl_rate,
            reset_cpl_rate=body.reset_cpl_rate,
            owner_telegram_id=body.owner_telegram_id,
        )
    except NotFound as exc:
        raise _not_found(exc) from exc
    except InvalidInput as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, exc.public_message
        ) from exc
    return PartnerOut.model_validate(view)


@admin_router.post("/{partner_id}/billing", response_model=PartnerOut)
async def partner_topup(
    body: BillingTopupIn,
    use_case: PartnerTopUpDep,
    partner_id: PartnerIdPath,
) -> PartnerOut:
    """Пополнение/корректировка баланса (атомарный `UPDATE ... RETURNING`)."""
    try:
        view = await use_case.execute(
            partner_id, body.amount, comment=body.comment
        )
    except NotFound as exc:
        raise _not_found(exc) from exc
    except ValueError as exc:  # нулевая сумма (схема ловит раньше)
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)
        ) from exc
    return PartnerOut.model_validate(view)


# =========================================================================
# Partner API: /api/partner
# =========================================================================

router = APIRouter(route_class=DishkaRoute, prefix="/api/partner", tags=["partners"])


@router.get("/me", response_model=PartnerMeOut)
async def partner_me(
    auth: PartnerAuthDep, use_case: PartnerOverviewDep
) -> PartnerMeOut:
    """Состояние своего счёта для ЛК (`X-API-Key` → только свой партнёр)."""
    partner_id = auth.partner_id
    try:
        overview = await use_case.execute(partner_id)
    except NotFound as exc:
        raise _not_found(exc) from exc
    return PartnerMeOut(
        id=overview.id,
        name=overview.name,
        plan=overview.plan,
        daily_quota=overview.daily_quota,
        claimed_today=overview.claimed_today,
        quota_remaining=overview.quota_remaining,
        balance=overview.balance,
        cpl_cost=overview.cpl_cost,
        key_prefix=None,
        placement_slots=overview.placement_slots,
        placement_slots_used=overview.placement_slots_used,
    )


@router.get(
    "/leads", response_model=list[PartnerLeadPublicOut | PartnerLeadOwnedOut]
)
async def partner_leads(
    auth: PartnerAuthDep,
    use_case: PartnerLeadsDep,
    rate: RateLimitDep,
    mine: bool = Query(default=False),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> list[PartnerLeadPublicOut | PartnerLeadOwnedOut]:
    """Пул (без PII) или мои лиды (с PII), `mine` фильтрует по партнёру."""
    partner_id = auth.partner_id
    await enforce_rate_limit(rate, partner_id)
    leads = await use_case.execute(
        partner_id, mine=mine, limit=limit, offset=offset
    )
    if mine:
        return [PartnerLeadOwnedOut.model_validate(lead) for lead in leads]
    return [PartnerLeadPublicOut.model_validate(lead) for lead in leads]


@router.post("/leads/batch", response_model=BatchClaimOut)
async def claim_leads_batch(
    body: BatchClaimIn,
    auth: PartnerAuthDep,
    use_case: ClaimBatchDep,
    read_leads: ReadLeadsDep,
    rate: RateLimitDep,
) -> BatchClaimOut:
    """Забрать до N лидов (N ≤ 10) атомарно, одним `SKIP LOCKED`-запросом."""
    partner_id = auth.partner_id
    await enforce_rate_limit(rate, partner_id)
    result = await use_case.execute(partner_id, body.limit)
    _raise_for_claim_reason(result.reason)
    leads = await read_leads.execute(result.lead_ids)
    return BatchClaimOut(
        claimed=[_claim_out(partner_id, lead) for lead in leads]
    )


@router.get("/billing", response_model=BillingOut)
async def partner_billing(
    auth: PartnerAuthDep, use_case: PartnerBillingDep, rate: RateLimitDep
) -> BillingOut:
    """План, баланс, расход квоты и каталог тарифов."""
    partner_id = auth.partner_id
    await enforce_rate_limit(rate, partner_id)
    try:
        billing = await use_case.execute(partner_id)
    except NotFound as exc:
        raise _not_found(exc) from exc
    overview = billing.overview
    return BillingOut(
        plan=overview.plan,
        daily_quota=overview.daily_quota,
        claimed_today=overview.claimed_today,
        quota_remaining=overview.quota_remaining,
        balance=overview.balance,
        cpl_cost=overview.cpl_cost,
        plans=[
            PlanInfo(
                key=plan.key,
                title=plan.title,
                daily_quota=plan.daily_quota,
                cpl_cost=plan.cpl_cost,
                description=plan.description,
            )
            for plan in billing.plans
        ],
    )


@router.get("/market", response_model=MarketOut)
async def partner_market(
    auth: PartnerAuthDep,
    use_case: PartnerMarketDep,
    rate: RateLimitDep,
    make: str | None = Query(default=None, max_length=64),
    model: str | None = Query(default=None, max_length=64),
    limit: int = Query(default=10, ge=1, le=50),
) -> MarketOut:
    """Сводка рынка (объём, мин/медиана/макс) + выгодные лоты.

    Инструмент сотрудников партнёра: без `make` — срез по всему каталогу
    (общая медиана, без группировки по моделям). Доступен только по
    API-ключу партнёра (`X-API-Key`), обычным пользователям не виден.
    """
    partner_id = auth.partner_id
    await enforce_rate_limit(rate, partner_id)
    report = await use_case.execute(make=make, model=model, deals_limit=limit)
    summary = report.summary
    return MarketOut(
        make=summary.make,
        model=summary.model,
        count=summary.count,
        min_price=summary.min_price,
        max_price=summary.max_price,
        median_price=summary.median_price,
        deals=[MarketDealOut.model_validate(deal) for deal in report.deals],
    )


@router.post("/leads/{lead_id}/claim", response_model=ClaimOut)
async def claim_lead(
    auth: PartnerAuthDep,
    use_case: ClaimLeadDep,
    read_leads: ReadLeadsDep,
    rate: RateLimitDep,
    lead_id: Annotated[int, Path(ge=1)],
) -> ClaimOut:
    """Эксклюзивно забрать лид: 409 занят, 404 нет, 429 квота, 402 баланс."""
    partner_id = auth.partner_id
    await enforce_rate_limit(rate, partner_id)
    result = await use_case.execute(partner_id, lead_id)
    if result.reason == "taken":
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Lead claim failed: taken"
        )
    if result.reason == "not_found":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Lead not found")
    _raise_for_claim_reason(result.reason)

    leads = await read_leads.execute(result.lead_ids)
    if not leads:
        logger.error(
            "partner_claim_invariant_violated partner_id=%s lead_id=%s reason=%s",
            partner_id,
            lead_id,
            result.reason,
        )
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR, "Internal error"
        )
    return _claim_out(partner_id, leads[0])


@router.post("/leads/{lead_id}/feedback", response_model=ClaimOut)
async def lead_feedback(
    body: FeedbackIn,
    auth: PartnerAuthDep,
    use_case: PartnerFeedbackDep,
    rate: RateLimitDep,
    lead_id: Annotated[int, Path(ge=1)],
) -> ClaimOut:
    """Статус качества своего лида (чужой лид → 404)."""
    partner_id = auth.partner_id
    await enforce_rate_limit(rate, partner_id)
    lead = await use_case.execute(partner_id, lead_id, body.status)
    if lead is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "Lead not found or not claimed by you"
        )
    return _claim_out(partner_id, lead)
