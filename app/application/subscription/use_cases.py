"""Тонкие сценарии подписки: доменные правила + подписи для транспорта."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.domain.subscription.policies import (
    PAID_TIERS,
    TIERS,
    TierConfig,
    activate_subscription,
    effective_tier,
    tier_config,
)


def current_tier(user: Any) -> str:
    """Действующий тариф с учётом срока."""
    return effective_tier(user)


def config_for(user: Any) -> TierConfig:
    """Лимиты и цены действующего тарифа."""
    return tier_config(user)


def activate(user: Any, tier: str, period_days: int) -> datetime:
    """Активировать/продлить подписку."""
    return activate_subscription(user, tier, period_days)


def all_tiers() -> dict[str, TierConfig]:
    """Справочник тарифов для витрины."""
    return TIERS


__all__ = [
    "PAID_TIERS",
    "TIERS",
    "TierConfig",
    "all_tiers",
    "activate",
    "config_for",
    "current_tier",
]
