"""Тарифы, CPL и правила claim'а — чистые решения без ORM.

Модуль не знает про сессию и SQL: цена лида и допустимость claim'а считаются
из простых значений, поэтому правила покрываются unit-тестами без БД.

Инварианты:
- `cpl_cost` — приоритет индивидуального `cpl_rate`, затем тариф плана,
  неизвестный план не роняет claim (fallback на «starter»);
- `can_claim` — сначала квота, затем баланс (порядок причин отказа важен:
  при исчерпанной квоте деньги не проверяются);
- квота проверяется на весь запрошенный объём (`n`), а не на один лид.
"""

from __future__ import annotations

import logging
from decimal import Decimal

logger = logging.getLogger(__name__)

PLAN_CATALOG: dict[str, dict] = {
    "starter": {
        "title": "Starter",
        "daily_quota": 10,
        "cpl_cost": Decimal("5.00"),
        "description": "Базовый доступ к пулу лидов",
    },
    "pro": {
        "title": "Pro",
        "daily_quota": 50,
        "cpl_cost": Decimal("3.00"),
        "description": "Приоритет в пуле, больше квота",
    },
    "salon": {
        "title": "Salon",
        "daily_quota": 200,
        "cpl_cost": Decimal("2.00"),
        "description": "Для салонов и дилеров",
    },
}

QUOTA_EXCEEDED = "quota_exceeded"
INSUFFICIENT_BALANCE = "insufficient_balance"


def cpl_cost(plan: str, cpl_rate: Decimal | None) -> Decimal:
    """CPL: индивидуальный `cpl_rate` или тариф плана.

    Args:
        plan: Ключ тарифа (`PLAN_CATALOG`).
        cpl_rate: Индивидуальная ставка партнёра, если задана.

    Returns:
        Цена одного лида.

    Неизвестный план логируется и считается по «starter»: claim из-за
    опечатки в тарифе падать не должен.
    """
    if cpl_rate is not None:
        return Decimal(cpl_rate)
    spec = PLAN_CATALOG.get(plan)
    if spec is None:
        logger.error("partner_unknown_plan plan=%s", plan)
        return Decimal(PLAN_CATALOG["starter"]["cpl_cost"])
    return Decimal(spec["cpl_cost"])


def can_claim(
    balance: Decimal,
    cost: Decimal,
    quota: int | None,
    used: int,
    n: int = 1,
) -> str | None:
    """Проверить право на claim `n` лидов.

    Args:
        balance: Текущий баланс партнёра.
        cost: Цена одного лида.
        quota: Дневная квота (`None` — без ограничения).
        used: Сколько лидов уже забрано с 00:00 UTC.
        n: Сколько лидов собираемся забрать.

    Returns:
        Причина отказа (`quota_exceeded` / `insufficient_balance`) или `None`.
    """
    if quota is not None and used + n > quota:
        return QUOTA_EXCEEDED
    if balance < cost * n:
        return INSUFFICIENT_BALANCE
    return None


def should_reactivate(kind: str, balance: Decimal, status: str) -> bool:
    """Нужно ли вернуть партнёра в строй после движения по счёту.

    Авто-разбан только для пополнения (`kind="topup"`), которое вывело
    баланс в плюс; корректировки статус не меняют.
    """
    return kind == "topup" and balance > 0 and status == "suspended"
