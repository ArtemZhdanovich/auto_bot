"""SQLAlchemy-репозиторий платежей и подписки пользователя.

Реализует `app.application.payments.use_cases.PaymentRepository`: сценарии
не знают про ORM, а деньги (сумма, срок) приходят уже посчитанными.

Инварианты:
- `create_pending` фиксирует строку сразу: инвойс ссылается на `payment.id`;
- `mark_paid` + `save_subscription` живут в одной транзакции, `commit`
  вызывается сценарием один раз (без «полуоплаченных» подписок);
- повторный `mark_paid` того же платежа не создаёт вторую строку.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.application.dto.payments import PaymentRecord, SubscriptionState
from app.application.dto.payments import STARS_CURRENCY, STARS_PROVIDER
from app.infrastructure.db.models import Payment, User


class SqlAlchemyPaymentRepository:
    """`PaymentRepository` на `AsyncSession`."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_pending(
        self,
        *,
        user_id: int,
        amount: int,
        tier: str,
        period_days: int,
    ) -> int:
        """Создать pending-платёж Stars и вернуть его id."""
        payment = Payment(
            user_id=user_id,
            amount=amount,
            currency=STARS_CURRENCY,
            provider=STARS_PROVIDER,
            status="pending",
            tier=tier,
            period_days=period_days,
        )
        self._session.add(payment)
        await self._session.commit()
        await self._session.refresh(payment)
        return payment.id

    async def get_payment(self, payment_id: int) -> PaymentRecord | None:
        """Снимок платежа или `None`, если платежа нет."""
        payment = await self._session.get(Payment, payment_id)
        if payment is None:
            return None
        return PaymentRecord(
            id=payment.id,
            user_id=payment.user_id,
            status=payment.status,
            tier=payment.tier,
            period_days=payment.period_days,
            amount=payment.amount,
        )

    async def get_subscription(self, user_id: int) -> SubscriptionState | None:
        """Подписка пользователя или `None`, если пользователя нет."""
        user = await self._session.get(User, user_id)
        if user is None:
            return None
        return SubscriptionState(
            subscription_tier=user.subscription_tier,
            subscription_until=user.subscription_until,
        )

    async def mark_paid(
        self,
        payment_id: int,
        *,
        provider_payment_id: str,
        paid_at: datetime,
    ) -> None:
        """Пометить платёж оплаченным в текущей транзакции."""
        payment = await self._session.get(Payment, payment_id)
        if payment is None:  # сценарий читал этот платёж в той же сессии
            raise LookupError(f"payment {payment_id} not found")
        payment.status = "paid"
        payment.paid_at = paid_at
        payment.provider_payment_id = provider_payment_id

    async def save_subscription(
        self, user_id: int, *, tier: str, until: datetime
    ) -> None:
        """Записать тариф и срок подписки в текущей транзакции."""
        user = await self._session.get(User, user_id)
        if user is None:  # сценарий читал этого пользователя в той же сессии
            raise LookupError(f"user {user_id} not found")
        user.subscription_tier = tier
        user.subscription_until = until

    async def commit(self) -> None:
        """Зафиксировать платёж и подписку одной транзакцией."""
        await self._session.commit()
