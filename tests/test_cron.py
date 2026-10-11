"""Тесты cron-матчера (app/services/cron.py) для sources.schedule_cron."""

from datetime import UTC, datetime

from app.services.cron import cron_matches


def _dt(minute: int, hour: int, day: int = 15, month: int = 6) -> datetime:
    # 15.06.2026 — понедельник
    return datetime(2026, month, day, hour, minute, tzinfo=UTC)


def test_every_minute():
    assert cron_matches("* * * * *", _dt(0, 0))
    assert cron_matches("*/1 * * * *", _dt(37, 13))


def test_daily_at_hour():
    expr = "0 9 * * *"
    assert cron_matches(expr, _dt(0, 9))
    assert not cron_matches(expr, _dt(1, 9))
    assert not cron_matches(expr, _dt(0, 8))


def test_range_and_list():
    assert cron_matches("0 9-11 * * *", _dt(0, 10))
    assert not cron_matches("0 9-11 * * *", _dt(0, 12))
    assert cron_matches("30 6,20 * * *", _dt(30, 20))
    assert not cron_matches("30 6,20 * * *", _dt(30, 12))


def test_step_minutes():
    expr = "*/15 * * * *"
    assert cron_matches(expr, _dt(0, 5))
    assert cron_matches(expr, _dt(45, 5))
    assert not cron_matches(expr, _dt(10, 5))


def test_day_of_week():
    # 15.06.2026 — понедельник (cron dow=1); 21.06 — суббота (dow=6)
    assert cron_matches("0 12 * * 1", _dt(0, 12))
    assert not cron_matches("0 12 * * 1", _dt(0, 12, day=16))  # вторник
    assert cron_matches("0 12 * * 0", _dt(0, 12, day=21, month=6))  # воскресенье? нет — суббота
    # 21.06.2026 — воскресенье по факту: 15 пн → 21 вс. cron 0 = вс — совпадает
    assert not cron_matches("0 12 * * 6", _dt(0, 12, day=21, month=6))


def test_dom_or_dow_standard():
    # 13-е число ИЛИ понедельник (классический OR cron)
    assert cron_matches("0 12 13 * 1", _dt(0, 12))  # пн 15-е → dow сработал
    assert cron_matches("0 12 13 * 1", _dt(0, 12, day=13))  # 13-е
    assert not cron_matches("0 12 13 * 1", _dt(0, 12, day=16))  # вт, не 13-е


def test_month_filter():
    assert cron_matches("0 0 1 6 *", _dt(0, 0, day=1, month=6))
    assert not cron_matches("0 0 1 6 *", _dt(0, 0, day=1, month=7))


def test_bad_expr_returns_false():
    assert not cron_matches("не cron", _dt(0, 0))
    assert not cron_matches("0 9 * *", _dt(0, 9))  # 4 поля
    assert not cron_matches("0 9 * * 7", _dt(0, 9))  # dow 7 вне 0-6
    assert not cron_matches("61 * * * *", _dt(0, 0))  # минута 61 вне 0-59
