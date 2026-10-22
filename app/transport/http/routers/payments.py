"""Подписки и Telegram Stars (этап 6, ТЗ п. 8.7, 9.6, 10.2): тонкий транспорт.

- GET  /api/subscription        — статус (JWT, Mini App)
- POST /api/payments/stars      — инвойс Stars (JWT, Mini App → openInvoice)
- GET  /api/users/{id}/subscription — статус (internal, бот /subscribe)
- POST /api/payments/prepare    — pending-платёж (internal, бот sendInvoice)
- POST /api/payments/success    — активация по successful_payment (internal, бот)

Суммы, сроки и активация подписки — в `app.application.payments`; контроллер
разбирает ввод, зовёт `createInvoiceLink` и маппит DTO в схему.
"""


from __future__ import annotations
from app.transport.http.deps.dishka import DishkaRoute

import logging
from dataclasses import asdict

import httpx
from fastapi import APIRouter, Depends, HTTPException, status

from app.application.errors import Conflict, NotFound
from app.config import get_settings
from app.transport.http.deps.auth import CurrentUserDep
from app.transport.http.deps.internal import check_internal_token
from app.transport.http.deps.use_cases import (
    ConfirmPaymentDep,
    PendingPaymentDep,
    SubscriptionStatusDep,
    UserSubscriptionDep,
)
from app.transport.http.schemas.payment import (
    PaymentPrepareIn,
    PaymentPrepareOut,
    PaymentSuccessIn,
    StarsInvoiceOut,
    StarsInvoiceRequest,
    SubscriptionOut,
    UserSubscriptionOut,
)

logger = logging.getLogger(__name__)

router = APIRouter(route_class=DishkaRoute, prefix="/api", tags=["payments"])

TELEGRAM_API = "https://api.telegram.org"

TIER_TITLES = {"pro": "AutoPick Pro", "business": "AutoPick Business"}
TIER_DESCRIPTIONS = {
    "pro": "20 фильтров, 200 AI-поисков/день, instant-уведомления, price drop",
    "business": "Без лимитов, API, CRM-интеграция, приоритетная поддержка",
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
    """Период подписки из запроса Mini App → дни."""
    return 365 if period == "year" else 30


@router.get("/subscription", response_model=SubscriptionOut)
async def get_subscription(
    user: CurrentUserDep, use_case: SubscriptionStatusDep
) -> SubscriptionOut:
    """Статус подписки и лимиты тарифа для Mini App (ТЗ п. 9.2, 301)."""
    snapshot = await use_case.execute(user)
    return SubscriptionOut(**asdict(snapshot))


@router.post("/payments/stars", response_model=StarsInvoiceOut)
async def create_stars_invoice(
    body: StarsInvoiceRequest,
    user: CurrentUserDep,
    use_case: PendingPaymentDep,
) -> StarsInvoiceOut:
    """Инвойс Stars для Mini App: pending-платёж → createInvoiceLink (ТЗ п. 9.6)."""
    settings = get_settings()
    if not settings.bot_token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Bot token is not configured",
        )
    draft = await use_case.execute(
        user_id=user.telegram_id,
        tier=body.tier,
        period_days=_period_days(body.period),
    )
    url = await _create_invoice_link(
        bot_token=settings.bot_token.get_secret_value(),
        payload=f"sub:{draft.payment_id}",
        title=TIER_TITLES[body.tier],
        description=TIER_DESCRIPTIONS[body.tier],
        amount=draft.amount,
    )
    return StarsInvoiceOut(
        payment_id=draft.payment_id,
        invoice_url=url,
        amount=draft.amount,
        tier=draft.tier,
        period_days=draft.period_days,
    )


@router.get(
    "/users/{user_id}/subscription",
    response_model=UserSubscriptionOut,
    dependencies=[Depends(check_internal_token)],
)
async def get_user_subscription(
    user_id: int, use_case: UserSubscriptionDep
) -> UserSubscriptionOut:
    """Статус тарифа для бота (/subscribe)."""
    view = await use_case.execute(user_id)
    if view is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )
    return UserSubscriptionOut(
        user_id=view.user_id,
        tier=view.tier,
        subscription_until=view.subscription_until,
    )


@router.post(
    "/payments/prepare",
    response_model=PaymentPrepareOut,
    dependencies=[Depends(check_internal_token)],
)
async def prepare_payment(
    body: PaymentPrepareIn, use_case: PendingPaymentDep
) -> PaymentPrepareOut:
    """Создать pending-платёж; инвойс бот отправит через sendInvoice (ТЗ п. 8.7)."""
    draft = await use_case.execute(
        user_id=body.user_id,
        tier=body.tier,
        period_days=body.period_days,
    )
    period_label = "год" if draft.period_days >= 365 else "мес"
    return PaymentPrepareOut(
        payment_id=draft.payment_id,
        amount=draft.amount,
        tier=draft.tier,
        period_days=draft.period_days,
        title=TIER_TITLES[body.tier],
        description=(
            f"{TIER_DESCRIPTIONS[body.tier]} — {draft.amount} Stars/{period_label}"
        ),
    )


@router.post(
    "/payments/success",
    dependencies=[Depends(check_internal_token)],
)
async def payment_success(
    body: PaymentSuccessIn, use_case: ConfirmPaymentDep
) -> dict:
    """successful_payment от бота: платёж → paid, подписка активирована."""
    try:
        result = await use_case.execute(
            payment_id=body.payment_id,
            user_id=body.user_id,
            provider_payment_id=body.provider_payment_id,
        )
    except NotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=exc.public_message
        ) from exc
    except Conflict as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=exc.public_message
        ) from exc
    return {
        "activated": result.activated,
        "tier": result.tier,
        "subscription_until": result.subscription_until,
    }
