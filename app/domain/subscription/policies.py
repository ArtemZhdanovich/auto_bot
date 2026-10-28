"""Тарифы и подписки (этап 6, ТЗ п. 8.7, 10.2; прод-ревизия 2026-10).

Четыре экономические модели:

- **Free** — подбор вручную + демо: 1 AI-разбор цены в НЕДЕЛЮ (на все
  машины суммарно), дайджест подборки раз в неделю, данные чеков
  осмотра закрыты.
- **Pro** (500 Stars/мес, год 4800★ = −20%) — 30 разборов/день, чеки
  доступны, дайджест каждый день.
- **Business** (2500 Stars/мес) — разборы без лимита («глубокий» — с
  чеком осмотра в контексте), объявление прилетает СРАЗУ при публикации
  (instant), можно отправить агента на сделку. Тариф для перекупов,
  маклеров и салонов; клиенты партнёров получают его автоматически
  (бессрочно, `subscription_until IS NULL`).
- **Partner** — не пользовательский тариф, а роль: владелец/сотрудник
  партнёрского аккаунта получает Business автоматически (см. выше).
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Literal


ANNUAL_DISCOUNT = 0.20  # годовая подписка со скидкой 20% (ТЗ п. 10.2)

TierName = Literal["free", "pro", "business"]

# Порядок тарифов для апгрейд-логики (кому что показывать).
TIER_ORDER: tuple[str, ...] = ("free", "pro", "business")


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
        analyses_limit: int | None = None,
        analyses_window_days: int = 1,
        digest: str = "daily",
        inspection: bool = False,
        agent: bool = False,
    ) -> None:
        self.name = name
        self.max_filters = max_filters  # None = без лимита
        self.ai_daily_limit = ai_daily_limit  # None = без лимита
        self.instant = instant  # уведомление сразу при публикации
        self.price_stars_month = price_stars_month
        self.price_stars_year = int(price_stars_month * 12 * (1 - ANNUAL_DISCOUNT))
        # AI-разборы цены: лимит на окно (free — 1/неделю на всё,
        # pro — 30/день, business — без лимита).
        self.analyses_limit = analyses_limit
        self.analyses_window_days = analyses_window_days
        # Каденс дайджеста: weekly (free) / daily (pro) / instant (business).
        self.digest = digest
        # Данные чеков осмотра доступны только на pro/business.
        self.inspection = inspection
        # Отправка агента на сделку — только business.
        self.agent = agent

    def price(self, period_days: int) -> int:
        return self.price_stars_year if period_days >= 365 else self.price_stars_month


TIERS: dict[str, TierConfig] = {
    "free": TierConfig(
        "free",
        max_filters=3,
        ai_daily_limit=3,
        instant=False,
        price_stars_month=0,
        analyses_limit=1,
        analyses_window_days=7,
        digest="weekly",
        inspection=False,
        agent=False,
    ),
    "pro": TierConfig(
        "pro",
        max_filters=20,
        ai_daily_limit=200,
        instant=False,
        price_stars_month=500,
        analyses_limit=30,
        analyses_window_days=1,
        digest="daily",
        inspection=True,
        agent=False,
    ),
    "business": TierConfig(
        "business",
        max_filters=None,
        ai_daily_limit=None,
        instant=True,
        price_stars_month=2500,
        analyses_limit=None,
        digest="instant",
        inspection=True,
        agent=True,
    ),
}

PAID_TIERS = ("pro", "business")


def tier_rank(tier: str) -> int:
    """Позиция тарифа в иерархии (free < pro < business).

    Неизвестный тариф — минимальный ранг (безопасный дефолт).
    """
    return TIER_ORDER.index(tier) if tier in TIER_ORDER else 0


def can_upgrade(current: str, offered: str) -> bool:
    """Показывать ли кнопку тарифа `offered` пользователю на `current`.

    Это правило — источник правды для апселла и в Mini App, и в боте:
    бизнесу НЕ предлагаем про (раньше это был баг), бесплатному — оба
    платных, про — только business.
    """
    return tier_rank(offered) > tier_rank(current)


def _as_aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def effective_tier(user: Any) -> str:
    """Действующий тариф.

    - истёкшая подписка = free (ТЗ п. 8.7);
    - business с `subscription_until IS NULL` — бессрочный тариф,
      выданный через партнёрский контур (клиент партнёра): действует,
      пока партнёр активен (снятие — админом).
    """
    tier = getattr(user, "subscription_tier", None)
    until = getattr(user, "subscription_until", None)
    if tier not in PAID_TIERS:
        return "free"
    if until is None:
        # Бессрочный тариф: законно только для business (партнёрский
        # контур); «pro без даты» считаем просроченным артефактом.
        return tier if tier == "business" else "free"
    return tier if _as_aware(until) > datetime.now(timezone.utc) else "free"


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


def grant_partner_business(user: Any) -> None:
    """Выдать бессрочный Business (клиент/сотрудник партнёра).

    `subscription_until = None` + `tier = business` — специальное
    состояние, которое `effective_tier` считает вечным, а
    `subscription_check_job` не трогает.
    """
    user.subscription_tier = "business"
    user.subscription_until = None
