"""Тарифы и подписки (этап 6, ТЗ п. 8.7, 10.2).

Тарифы:
- Free: 3 фильтра, 20 AI-сообщений/день, только digest.
- Pro (500 Stars/мес, год 4800★ = −20%): 20 фильтров, 200 AI/день, instant, price drop.
- Business (2500 Stars/мес, год 24000★): без лимитов.
"""

from datetime import datetime, timedelta, timezone
from typing import Any


ANNUAL_DISCOUNT = 0.20  # годовая подписка со скидкой 20% (ТЗ п. 10.2)


class TierConfig:
    """Лимиты и цены одного тарифа."""

    def __init__(
        self,
        name: str,
        *,
        max_filters: int | None,
        ai_daily_limit: int | None,
        instant: bool,
        price_stars_month: int,
    ) -> None:
        self.name = name
        self.max_filters = max_filters  # None = без лимита
        self.ai_daily_limit = ai_daily_limit  # None = без лимита
        self.instant = instant
        self.price_stars_month = price_stars_month
        self.price_stars_year = int(price_stars_month * 12 * (1 - ANNUAL_DISCOUNT))

    def price(self, period_days: int) -> int:
        return self.price_stars_year if period_days >= 365 else self.price_stars_month


TIERS: dict[str, TierConfig] = {
    "free": TierConfig("free", max_filters=3, ai_daily_limit=20, instant=False, price_stars_month=0),
    "pro": TierConfig("pro", max_filters=20, ai_daily_limit=200, instant=True, price_stars_month=500),
    "business": TierConfig(
        "business", max_filters=None, ai_daily_limit=None, instant=True, price_stars_month=2500
    ),
}

PAID_TIERS = ("pro", "business")


def _as_aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def effective_tier(user: Any) -> str:
    """Действующий тариф: истёкшая подписка = free (ТЗ п. 8.7)."""
    tier = getattr(user, "subscription_tier", None)
    until = getattr(user, "subscription_until", None)
    if tier in PAID_TIERS and until is not None and _as_aware(until) > datetime.now(timezone.utc):
        return tier
    return "free"


def tier_config(user: Any) -> TierConfig:
    return TIERS[effective_tier(user)]


def activate_subscription(user: Any, tier: str, period_days: int) -> datetime:
    """Активировать/продлить подписку. Повторная оплата суммируется с текущей."""
    now = datetime.now(timezone.utc)
    until = getattr(user, "subscription_until", None)
    base = _as_aware(until) if until is not None and _as_aware(until) > now else now
    new_until = base + timedelta(days=period_days)
    user.subscription_tier = tier
    user.subscription_until = new_until
    return new_until
