"""Порты биллинга: хранилище платежей и субъект подписки."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from app.application.billing.dto import PaymentRecord, SubscriptionState


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


class SubscriptionSubject(Protocol):
    """Минимальный контракт субъекта подписки (ORM удовлетворяет)."""

    subscription_tier: str | None
    subscription_until: datetime | None
