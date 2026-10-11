"""Подписки и Telegram Stars (этап 6, ТЗ п. 8.7, 9.6, 10.2).

- GET  /api/subscription        — статус (JWT, Mini App)
- POST /api/payments/stars      — инвойс Stars (JWT, Mini App → openInvoice)
- GET  /api/users/{id}/subscription — статус (internal, бот /subscribe)
- POST /api/payments/prepare    — pending-платёж (internal, бот sendInvoice)
- POST /api/payments/success    — активация по successful_payment (internal, бот)
"""

import logging
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, status

from app.core.config import get_settings
from app.core.deps import CurrentUserDep, SessionDep
from app.core.deps_internal import check_internal_token
from app.db.models import Payment, User
from app.schemas.payment import (
    PaymentPrepareIn,
    PaymentPrepareOut,
    PaymentSuccessIn,
    StarsInvoiceOut,
    StarsInvoiceRequest,
    SubscriptionOut,
    UserSubscriptionOut,
)
from app.services.subscription import TIERS, activate_subscription, effective_tier

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["payments"])

TELEGRAM_API = "https://api.telegram.org"

TIER_TITLES = {"pro": "AutoPick Pro", "business": "AutoPick Business"}
TIER_DESCRIPTIONS = {
    "pro": "20 фильтров, 200 AI-сообщений/день, instant-уведомления, price drop",
    "business": "Без лимитов, API, CRM-интеграция, приоритетная поддержка",
}


def _prices() -> dict[str, int]:
    return {
        f"{tier}_{period}": cfg.price(30 if period == "month" else 365)
        for tier, cfg in TIERS.items()
        if cfg.price_stars_month > 0
        for period in ("month", "year")
    }


async def _create_invoice_link(
    bot_token: str, payload: str, title: str, description: str, amount: int
) -> str:
    """createInvoiceLink (currency=XTR). Вынесено для моков в тестах."""
    url = f"{TELEGRAM_API}/bot{bot_token}/createInvoiceLink"
    body = {
        "title": title,
        "description": description,
        "payload": payload,
        "currency": "XTR",
        "prices": [{"label": title, "amount": amount}],
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(url, json=body)
    data = resp.json()
    if resp.status_code != 200 or not data.get("ok"):
        logger.error("create_invoice_link_failed: %s", resp.text[:300])
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Telegram invoice creation failed",
        )
    return data["result"]


def _period_days(period: str) -> int:
    return 365 if period == "year" else 30


@router.get("/subscription", response_model=SubscriptionOut)
async def get_subscription(user: CurrentUserDep) -> SubscriptionOut:
    """Статус подписки и лимиты тарифа для Mini App (ТЗ п. 9.2, 301)."""
    cfg = TIERS[effective_tier(user)]
    until = user.subscription_until
    if until is not None and until.tzinfo is None:
        until = until.replace(tzinfo=timezone.utc)
    return SubscriptionOut(
        tier=cfg.name,
        paid_tier=user.subscription_tier or "free",
        subscription_until=until if cfg.name != "free" else None,
        max_filters=cfg.max_filters,
        ai_daily_limit=cfg.ai_daily_limit,
        instant=cfg.instant,
        prices=_prices(),
    )


@router.post("/payments/stars", response_model=StarsInvoiceOut)
async def create_stars_invoice(
    body: StarsInvoiceRequest, user: CurrentUserDep, session: SessionDep
) -> StarsInvoiceOut:
    """Инвойс Stars для Mini App: pending-платёж → createInvoiceLink (ТЗ п. 9.6)."""
    settings = get_settings()
    if not settings.bot_token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Bot token is not configured",
        )
    cfg = TIERS[body.tier]
    period_days = _period_days(body.period)
    amount = cfg.price(period_days)

    payment = Payment(
        user_id=user.telegram_id,
        amount=amount,
        currency="XTR",
        provider="stars",
        status="pending",
        tier=body.tier,
        period_days=period_days,
    )
    session.add(payment)
    await session.commit()
    await session.refresh(payment)

    title = TIER_TITLES[body.tier]
    url = await _create_invoice_link(
        bot_token=settings.bot_token,
        payload=f"sub:{payment.id}",
        title=title,
        description=TIER_DESCRIPTIONS[body.tier],
        amount=amount,
    )
    return StarsInvoiceOut(
        payment_id=payment.id,
        invoice_url=url,
        amount=amount,
        tier=body.tier,
        period_days=period_days,
    )


@router.get(
    "/users/{user_id}/subscription",
    response_model=UserSubscriptionOut,
    dependencies=[Depends(check_internal_token)],
)
async def get_user_subscription(user_id: int, session: SessionDep) -> UserSubscriptionOut:
    """Статус тарифа для бота (/subscribe)."""
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )
    return UserSubscriptionOut(
        user_id=user_id,
        tier=effective_tier(user),
        subscription_until=user.subscription_until,
    )


@router.post(
    "/payments/prepare",
    response_model=PaymentPrepareOut,
    dependencies=[Depends(check_internal_token)],
)
async def prepare_payment(
    body: PaymentPrepareIn, session: SessionDep
) -> PaymentPrepareOut:
    """Создать pending-платёж; инвойс бот отправит через sendInvoice (ТЗ п. 8.7)."""
    cfg = TIERS[body.tier]
    amount = cfg.price(body.period_days)
    payment = Payment(
        user_id=body.user_id,
        amount=amount,
        currency="XTR",
        provider="stars",
        status="pending",
        tier=body.tier,
        period_days=body.period_days,
    )
    session.add(payment)
    await session.commit()
    await session.refresh(payment)
    period_label = "год" if body.period_days >= 365 else "мес"
    return PaymentPrepareOut(
        payment_id=payment.id,
        amount=amount,
        tier=body.tier,
        period_days=body.period_days,
        title=TIER_TITLES[body.tier],
        description=f"{TIER_DESCRIPTIONS[body.tier]} — {amount} Stars/{period_label}",
    )


@router.post(
    "/payments/success",
    dependencies=[Depends(check_internal_token)],
)
async def payment_success(body: PaymentSuccessIn, session: SessionDep) -> dict:
    """successful_payment от бота: платёж → paid, подписка активирована (ТЗ п. 8.7)."""
    payment = await session.get(Payment, body.payment_id)
    if payment is None or payment.user_id != body.user_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Payment not found"
        )
    user = await session.get(User, body.user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )

    if payment.status == "paid":  # идемпотентность retry от Telegram
        return {
            "activated": True,
            "tier": payment.tier,
            "subscription_until": user.subscription_until,
        }
    if payment.tier is None or payment.period_days is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Payment is incomplete"
        )

    payment.status = "paid"
    payment.paid_at = datetime.now(timezone.utc)
    payment.provider_payment_id = body.provider_payment_id
    new_until = activate_subscription(user, payment.tier, payment.period_days)
    await session.commit()
    logger.info(
        "subscription_activated: user=%s tier=%s until=%s",
        body.user_id,
        payment.tier,
        new_until,
    )
    return {
        "activated": True,
        "tier": payment.tier,
        "subscription_until": user.subscription_until,
    }

