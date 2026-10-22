"""Правила уведомлений: тихие часы, матчинг, форматирование.

Чистые функции без БД, Redis и Telegram.
"""

from __future__ import annotations

import html
import logging
from datetime import datetime, time, timezone
from decimal import Decimal
from typing import Any

from app.domain.search.criteria import SearchCriteria

logger = logging.getLogger(__name__)


def notified_key(user_id: int, listing_id: int, kind: str = "new") -> str:
    """Для 'new' — исторический ключ без суффикса (совместимость), иначе с ним."""
    base = f"notified:{user_id}:{listing_id}"
    return base if kind == "new" else f"{base}:{kind}"


def parse_quiet_hours(quiet_hours: str | None) -> tuple[time, time] | None:
    if not quiet_hours or "-" not in quiet_hours:
        return None
    try:
        start_s, end_s = quiet_hours.split("-", 1)
        start = time.fromisoformat(start_s.strip())
        end = time.fromisoformat(end_s.strip())
    except ValueError:
        logger.warning("bad_quiet_hours: %r", quiet_hours)
        return None
    return start, end


def in_quiet_hours(
    quiet_hours: str | None,
    now: datetime | None = None,
    default: str | None = None,
) -> bool:
    parsed = parse_quiet_hours(quiet_hours or default)
    if parsed is None:
        return False
    start, end = parsed
    current = (now or datetime.now(timezone.utc)).time().replace(tzinfo=None)
    if start <= end:
        return start <= current < end
    # ночной интервал через полночь (23:00-08:00)
    return current >= start or current < end


def _norm(value: str | None) -> str:
    return (value or "").strip().lower()


def matches_criteria(listing: Any, criteria: SearchCriteria) -> bool:
    """Проверка одного объявления по SearchCriteria (NULL в фильтре = не важно)."""
    if criteria.make and criteria.make.lower() not in _norm(listing.make):
        return False
    if criteria.model and criteria.model.lower() not in _norm(listing.model):
        return False
    if criteria.year_from and (listing.year or 0) < criteria.year_from:
        return False
    if criteria.year_to and listing.year and listing.year > criteria.year_to:
        return False
    if criteria.price_min and (listing.price or 0) < criteria.price_min:
        return False
    if criteria.price_max and listing.price and listing.price > criteria.price_max:
        return False
    if criteria.mileage_max and (listing.mileage_km or 0) > criteria.mileage_max:
        return False
    if criteria.fuel and criteria.fuel.lower() != _norm(getattr(listing, "fuel", None)):
        return False
    if criteria.sources and listing.source not in criteria.sources:
        return False
    return True


def digest_key(user_id: int) -> str:
    return f"digest:{user_id}"


def _fmt_price(price: Any, currency: str | None) -> str:
    if price is None:
        return "цена по запросу"
    try:
        amount = f"{Decimal(str(price)):,.0f}".replace(",", " ")
    except Exception:
        amount = str(price)
    cur = {"USD": "$", "KRW": "₩", "EUR": "€", "CNY": "¥", "JPY": "¥"}.get(
        (currency or "USD").upper(), currency or "$"
    )
    return f"{amount} {cur}"


def format_listing_card(listing: Any) -> str:
    """HTML-карточка объявления (фото/кнопки добавляет отправитель)."""
    # Переведённый тайтл (фаза 5) предпочтим «make model year»;
    # в тестах/бот-пейлоадах поля может не быть — getattr.
    title = getattr(listing, "title_ru", None) or " ".join(
        str(part) for part in (listing.make, listing.model, listing.year) if part
    )
    lines = [f"🚗 <b>{html.escape(title)}</b>" if title else "🚗 <b>Объявление</b>"]
    lines.append(f"💰 {_fmt_price(listing.price, listing.currency)}")
    if listing.mileage_km:
        lines.append(f"📏 {listing.mileage_km:,} км".replace(",", " "))
    lines.append(f"🔗 {html.escape(str(listing.source).capitalize())}")
    lines.append(html.escape(str(listing.url)))
    return "\n".join(lines)
