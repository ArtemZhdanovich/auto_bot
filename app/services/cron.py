"""Мини-парсер cron-выражений для расписания парсинга (sources.schedule_cron).

Формат: 5 полей `минута час день_месяца месяц день_недели`.
Поддержка: `*`, `*/n`, `a`, `a-b`, `a-b/n`, списки через запятую.
Числовые значения без имён (JAN/MON и т.п.); dow: 0=воскресенье … 6=суббота.
Если ограничены и dom, и dow — объединяются через OR (стандарт cron).
Невалидное выражение → False (источник просто не парсится, воркер не падает).
"""

import logging
from datetime import datetime

logger = logging.getLogger(__name__)

_FIELD_BOUNDS = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 6))


def _parse_field(field: str, low: int, high: int) -> set[int]:
    values: set[int] = set()
    for part in field.split(","):
        part = part.strip()
        if not part:
            raise ValueError(f"empty cron field part in {field!r}")
        step = 1
        range_part = part
        if "/" in part:
            range_part, step_s = part.split("/", 1)
            step = int(step_s)
            if step < 1:
                raise ValueError(f"bad cron step in {part!r}")
        if range_part == "*":
            start, end = low, high
        elif "-" in range_part:
            start_s, end_s = range_part.split("-", 1)
            start, end = int(start_s), int(end_s)
        else:
            start = end = int(range_part)
        if start < low or end > high or start > end:
            raise ValueError(f"cron field out of range: {part!r}")
        values.update(range(start, end + 1, step))
    return values


def _cron_dow(dt: datetime) -> int:
    """Python weekday (пн=0) → cron dow (вс=0)."""
    return (dt.weekday() + 1) % 7


def cron_matches(expr: str, dt: datetime) -> bool:
    """Совпадает ли cron-выражение с моментом dt (UTC, точность до минуты)."""
    try:
        fields = expr.split()
        if len(fields) != 5:
            raise ValueError(f"expected 5 fields, got {len(fields)}")
        minute, hour, dom, month, dow = (
            _parse_field(f, lo, hi) for f, (lo, hi) in zip(fields, _FIELD_BOUNDS)
        )
    except (ValueError, TypeError) as exc:
        logger.warning("bad_cron_expr: %s (%s)", expr, exc)
        return False

    dom_restricted = dom != set(range(1, 32))
    dow_restricted = dow != set(range(7))
    if dom_restricted and dow_restricted:
        day_ok = dt.day in dom or _cron_dow(dt) in dow
    else:
        day_ok = dt.day in dom and _cron_dow(dt) in dow
    return dt.minute in minute and dt.hour in hour and dt.month in month and day_ok
