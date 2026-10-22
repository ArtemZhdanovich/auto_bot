"""Сценарии платежей Telegram Stars и статуса подписки.

Слой не знает про SQLAlchemy/FastAPI: порт хранилища, сквозной порт
событий и чистые правила `app.domain.subscription.policies`.

Гарантии (деньги):
- сумма считается один раз в сценарии (`TierConfig.price`), транспорт её
  только показывает;
- повторный `successful_payment` идемпотентен: `status == "paid"` → подписка
  не продлевается второй раз;
- платёж и подписка меняются в одной транзакции (`commit` порта).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from app.application.dto.payments import (
    PaymentConfirmation,
    PaymentDraft,
    PaymentRecord,
    SubscriptionSnapshot,
    SubscriptionState,
    UserSubscriptionView,
)

# Константы провайдера живут в dto (их юзает и репозиторий).
from app.application.dto.payments import (  # noqa: F401 — ре-экспорт
    STARS_CURRENCY,
    STARS_PROVIDER,
)
from app.application.errors import Conflict, InvalidInput, NotFound
from app.application.shared.ports import EventTracker
from app.domain.analytics.events import EVENT_SUBSCRIPTION_ACTIVATED
from app.domain.subscription.policies import (
    TIERS,
    activate_subscription,
    effective_tier,
    tier_config,
)

logger = logging.getLogger(__name__)



class PaymentRepository(Protocol):
    """Хранилище платежей и подписки пользователя."""

    async def create_pending(
        self,
        *,
        user_id: int,
        amount: int,
        tier: str,
        period_days: int,
    ) -> int:
        """Создать pending-платёж и вернуть его id (фиксирует строку)."""

    async def get_payment(self, payment_id: int) -> PaymentRecord | None:
        """Снимок платежа или `None`, если платежа нет."""

    async def get_subscription(self, user_id: int) -> SubscriptionState | None:
        """Подписка пользователя или `None`, если пользователя нет."""

    async def mark_paid(
        self,
        payment_id: int,
        *,
        provider_payment_id: str,
        paid_at: datetime,
    ) -> None:
        """Пометить платёж оплаченным (в текущей транзакции)."""

    async def save_subscription(
        self, user_id: int, *, tier: str, until: datetime
    ) -> None:
        """Записать тариф и срок подписки (в текущей транзакции)."""

    async def commit(self) -> None:
        """Зафиксировать платёж и подписку одной транзакцией."""


@dataclass(slots=True)
class _Subject:
    """Изменяемый субъект подписки для доменной политики."""

    subscription_tier: str | None
    subscription_until: datetime | None


def price_list() -> dict[str, int]:
    """Цены Stars по платным тарифам: `{"pro_month": 500, ...}`."""
    return {
        f"{tier}_{period}": cfg.price(30 if period == "month" else 365)
        for tier, cfg in TIERS.items()
        if cfg.price_stars_month > 0
        for period in ("month", "year")
    }


def _as_utc(value: datetime | None) -> datetime | None:
    """Наивное время из БД трактуем как UTC (как прежний транспорт)."""
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


class GetSubscriptionStatus:
    """Статус и лимиты действующего тарифа (Mini App)."""

    async def execute(self, user: Any) -> SubscriptionSnapshot:
        """Собрать статус по субъекту подписки (ORM/снимок)."""
        cfg = tier_config(user)
        until = _as_utc(getattr(user, "subscription_until", None))
        return SubscriptionSnapshot(
            tier=cfg.name,
            paid_tier=getattr(user, "subscription_tier", None) or "free",
            subscription_until=until if cfg.name != "free" else None,
            max_filters=cfg.max_filters,
            ai_daily_limit=cfg.ai_daily_limit,
            instant=cfg.instant,
            prices=price_list(),
        )


class GetUserSubscription:
    """Статус тарифа по id пользователя (internal, бот `/subscribe`)."""

    def __init__(self, payments: PaymentRepository) -> None:
        self._payments = payments

    async def execute(self, user_id: int) -> UserSubscriptionView | None:
        """`None` — пользователя нет (транспорт отдаёт 404)."""
        state = await self._payments.get_subscription(user_id)
        if state is None:
            return None
        return UserSubscriptionView(
            user_id=user_id,
            tier=effective_tier(state),
            subscription_until=state.subscription_until,
        )


class CreatePendingPayment:
    """Создать pending-платёж Stars с ценой тарифа на срок."""

    def __init__(self, payments: PaymentRepository) -> None:
        self._payments = payments

    async def execute(
        self, *, user_id: int, tier: str, period_days: int
    ) -> PaymentDraft:
        """Создать платёж.

        Raises:
            InvalidInput: Неизвестный или бесплатный тариф.
        """
        cfg = TIERS.get(tier)
        if cfg is None or cfg.price_stars_month <= 0:
            raise InvalidInput(f"Unknown paid tier: {tier}")
        amount = cfg.price(period_days)
        payment_id = await self._payments.create_pending(
            user_id=user_id, amount=amount, tier=tier, period_days=period_days
        )
        return PaymentDraft(
            payment_id=payment_id,
            amount=amount,
            tier=tier,
            period_days=period_days,
        )


class ConfirmPayment:
    """`successful_payment`: платёж → paid, подписка активирована."""

    def __init__(
        self,
        payments: PaymentRepository,
        events: EventTracker | None = None,
    ) -> None:
        self._payments = payments
        self._events = events

    async def execute(
        self,
        *,
        payment_id: int,
        user_id: int,
        provider_payment_id: str,
    ) -> PaymentConfirmation:
        """Подтвердить платёж (идемпотентно по статусу `paid`).

        Raises:
            NotFound: Платежа/пользователя нет или платёж чужой.
            Conflict: В платеже нет тарифа/срока.
        """
        record = await self._payments.get_payment(payment_id)
        if record is None or record.user_id != user_id:
            raise NotFound("Payment not found")
        state = await self._payments.get_subscription(user_id)
        if state is None:
            raise NotFound("User not found")

        if record.status == "paid":  # ретрай successful_payment от Telegram
            return PaymentConfirmation(
                activated=True,
                tier=record.tier,
                subscription_until=state.subscription_until,
            )
        if record.tier is None or record.period_days is None:
            raise Conflict("Payment is incomplete")

        subject = _Subject(
            subscription_tier=state.subscription_tier,
            subscription_until=state.subscription_until,
        )
        new_until = activate_subscription(
            subject, record.tier, record.period_days
        )
        await self._payments.mark_paid(
            payment_id,
            provider_payment_id=provider_payment_id,
            paid_at=datetime.now(UTC),
        )
        await self._payments.save_subscription(
            user_id, tier=record.tier, until=new_until
        )
        await self._payments.commit()

        if self._events is not None:
            await self._events(
                user_id,
                EVENT_SUBSCRIPTION_ACTIVATED,
                {
                    "tier": record.tier,
                    "days": record.period_days,
                    "stars": float(record.amount),
                },
            )
        logger.info(
            "subscription_activated: user=%s tier=%s until=%s",
            user_id,
            record.tier,
            new_until,
        )
        return PaymentConfirmation(
            activated=True,
            tier=record.tier,
            subscription_until=new_until,
        )


__all__ = [
    "ConfirmPayment",
    "CreatePendingPayment",
    "GetSubscriptionStatus",
    "GetUserSubscription",
    "PaymentRepository",
    "STARS_CURRENCY",
    "STARS_PROVIDER",
    "price_list",
]
