"""Тесты логики уведомлений (этап 4, ТЗ п. 8.6): матчинг, антидубли,
тихие часы, digest-буфер, карточка."""

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.db.models import Listing
from app.schemas.criteria import SearchCriteria
from app.services.notify import (
    buffer_for_digest,
    clear_digest,
    format_listing_card,
    in_quiet_hours,
    mark_sent,
    matches_criteria,
    notified_key,
    parse_quiet_hours,
    read_digest,
    was_sent,
)


def _listing(**kwargs) -> Listing:
    defaults = dict(
        id=1,
        source="encar",
        source_id="A1",
        url="https://encar.com/1",
        make="Hyundai",
        model="Palisade",
        year=2022,
        price=Decimal("37000"),
        currency="USD",
        mileage_km=42000,
    )
    defaults.update(kwargs)
    return Listing(**defaults)


# --- матчинг критериев ------------------------------------------------------


def test_matches_full():
    crit = SearchCriteria(make="hyundai", year_from=2021, year_to=2023, price_max=40000)
    assert matches_criteria(_listing(), crit) is True


def test_make_mismatch():
    crit = SearchCriteria(make="kia")
    assert matches_criteria(_listing(), crit) is False


def test_year_out_of_range():
    assert matches_criteria(_listing(), SearchCriteria(year_from=2023)) is False
    assert matches_criteria(_listing(year=2023), SearchCriteria(year_from=2023)) is True


def test_price_mismatch():
    crit = SearchCriteria(price_min=40000)
    assert matches_criteria(_listing(), crit) is False


def test_mileage_mismatch():
    assert matches_criteria(_listing(), SearchCriteria(mileage_max=10000)) is False


def test_source_restriction():
    assert matches_criteria(_listing(), SearchCriteria(sources=["kcar"])) is False
    assert matches_criteria(_listing(), SearchCriteria(sources=["encar"])) is True


def test_empty_criteria_matches_anything():
    assert matches_criteria(_listing(), SearchCriteria()) is True


# --- антидубли ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_mark_sent_writes_log_and_redis(fake_session, fake_redis):
    await mark_sent(fake_session, fake_redis, user_id=100, listing_id=5)

    assert fake_session.committed is True
    # SentLog пишется через pg_insert (execute), а не session.add
    assert any("sent_log" in str(stmt) for stmt in fake_session.executed)

    assert await was_sent(fake_redis, 100, 5) is True
    assert fake_redis.ttls[notified_key(100, 5)] == 60 * 60 * 24 * 7


@pytest.mark.asyncio
async def test_was_sent_false_initially(fake_redis):
    assert await was_sent(fake_redis, 1, 2) is False


# --- тихие часы ---------------------------------------------------------------


def test_quiet_hours_overnight():
    assert parse_quiet_hours("23:00-08:00") is not None
    late = datetime(2026, 1, 1, 23, 30, tzinfo=timezone.utc)
    early = datetime(2026, 1, 1, 7, 0, tzinfo=timezone.utc)
    day = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    assert in_quiet_hours("23:00-08:00", now=late) is True
    assert in_quiet_hours("23:00-08:00", now=early) is True
    assert in_quiet_hours("23:00-08:00", now=day) is False


def test_quiet_hours_same_day_interval():
    noon = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    assert in_quiet_hours("09:00-18:00", now=noon) is True
    assert in_quiet_hours(None, now=noon) is False


def test_quiet_hours_bad_string():
    assert parse_quiet_hours("garbage") is None
    assert parse_quiet_hours(None) is None
    assert in_quiet_hours("garbage") is False


# --- digest-буфер --------------------------------------------------------------


@pytest.mark.asyncio
async def test_digest_buffer_roundtrip(fake_redis):
    await buffer_for_digest(fake_redis, 100, 1)
    await buffer_for_digest(fake_redis, 100, 2)
    await buffer_for_digest(fake_redis, 100, 3)
    await buffer_for_digest(fake_redis, 200, 9)  # другой пользователь

    assert await read_digest(fake_redis, 100) == [1, 2, 3]
    assert await read_digest(fake_redis, 100, max_cards=2) == [2, 3]

    await clear_digest(fake_redis, 100)
    assert await read_digest(fake_redis, 100) == []
    assert await read_digest(fake_redis, 200) == [9]  # чужой буфер не тронут


# --- карточка ------------------------------------------------------------------


def test_format_card():
    card = format_listing_card(_listing())
    assert "<b>Hyundai Palisade 2022</b>" in card
    assert "37 000 $" in card
    assert "42 000 км" in card
    assert "Encar" in card
    assert "https://encar.com/1" in card


def test_format_card_escapes_html():
    listing = _listing(model="<script>")
    card = format_listing_card(listing)
    assert "<script>" not in card
    assert "&lt;script&gt;" in card


def test_format_card_no_price():
    card = format_listing_card(_listing(price=None, currency=None, mileage_km=None))
    assert "цена по запросу" in card
    assert "км" not in card


def test_format_card_prefers_translated_title():
    # Перевод тайтла (PLAN.md, фаза 5): title_ru важнее «make model year»
    listing = _listing(title_ru="Хёндэ Палисад 2022")
    card = format_listing_card(listing)
    assert "<b>Хёндэ Палисад 2022</b>" in card
    assert "Hyundai Palisade 2022" not in card


def test_format_card_without_title_field_keeps_make_model():
    # title_ru не задан (NULL в БД или старый пейлоад) — как раньше
    listing = _listing()
    card = format_listing_card(listing)
    assert "<b>Hyundai Palisade 2022</b>" in card

async def test_mark_sent_with_kind(fake_redis, fake_session):
    from app.services.notify import event_audience

    await mark_sent(fake_session, fake_redis, 100, 5, kind="price_drop")
    assert await fake_redis.get(notified_key(100, 5, "price_drop")) == "1"
    # строка пишется в sent_log (kind — параметр insert, не виден в str(stmt))
    assert any("sent_log" in str(stmt) for stmt in fake_session.executed)

    fake_session.set_result([100, 300])
    assert await event_audience(fake_session, 5) == [100, 300]
