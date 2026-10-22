"""Тесты продуктовой аналитики (P1-7): analytics_events, хуки событий."""

import pytest

from app.infrastructure.db.models import AnalyticsEvent, Payment, User
from app.transport.http.deps.internal import internal_token
from app.infrastructure.services import analytics
from app.infrastructure.services.analytics import track
from app.infrastructure.services.users import upsert_user
from conftest import FakeSession, install_test_container


class _BrokenSession(FakeSession):
    """Сессия с падающим commit — track() не должен бросать."""

    async def commit(self) -> None:
        raise RuntimeError("db down")


@pytest.mark.asyncio
async def test_track_inserts_event(fake_session):
    await track(fake_session, 42, "search_completed", {"results": 5})
    assert fake_session.committed is True
    events = [x for x in fake_session.added if isinstance(x, AnalyticsEvent)]
    assert len(events) == 1
    assert events[0].user_id == 42
    assert events[0].name == "search_completed"
    assert events[0].props == {"results": 5}


@pytest.mark.asyncio
async def test_track_never_raises(fake_session):
    session = _BrokenSession()
    await track(session, 1, "dialog_message")  # не бросает, несмотря на RuntimeError
    assert session.committed is False


@pytest.mark.asyncio
async def test_upsert_user_tracks_registration_once(fake_session):
    await upsert_user(fake_session, telegram_id=777, username="u", source="bot")
    events = [x for x in fake_session.added if isinstance(x, AnalyticsEvent)]
    assert len(events) == 1
    assert events[0].name == analytics.EVENT_USER_REGISTERED
    assert events[0].props == {"source": "bot"}

    # повторный upsert того же юзера — событие НЕ дублируется
    await upsert_user(fake_session, telegram_id=777, username="u2")
    events = [x for x in fake_session.added if isinstance(x, AnalyticsEvent)]
    assert len(events) == 1


@pytest.mark.asyncio
async def test_upsert_user_existing_no_event(fake_session):
    existing = User(telegram_id=555, username="old")
    fake_session.add(existing)
    await upsert_user(fake_session, telegram_id=555, username="new")
    events = [x for x in fake_session.added if isinstance(x, AnalyticsEvent)]
    assert events == []


@pytest.mark.asyncio
async def test_payment_success_tracks_activation(fake_session):
    """Оплата → subscription_activated с tier/days/stars (идемпотентно)."""
    from app.main_api import app
    from httpx import ASGITransport, AsyncClient
    
    payment = Payment(
        id=10,
        user_id=9,
        amount=100,
        currency="XTR",
        status="pending",
        tier="pro",
        period_days=30,
    )
    user = User(telegram_id=9)
    fake_session.add(payment)
    fake_session.add(user)

    headers = {"X-Internal-Token": internal_token()}
    _prev = app.state.dishka_container
    _container = install_test_container(app, session=fake_session)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as http:
            resp = await http.post(
                "/api/payments/success",
                json={"payment_id": 10, "user_id": 9, "provider_payment_id": "chg_1", "amount": 100},
                headers=headers,
            )
    finally:
        app.state.dishka_container = _prev
        await _container.close()
    assert resp.status_code == 200
    events = [x for x in fake_session.added if isinstance(x, AnalyticsEvent)]
    assert len(events) == 1
    assert events[0].name == analytics.EVENT_SUBSCRIPTION_ACTIVATED
    assert events[0].props == {"tier": "pro", "days": 30, "stars": 100.0}

    # идемпотентность: повторный success — события не добавляет
    _prev = app.state.dishka_container
    _container = install_test_container(app, session=fake_session)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as http:
            await http.post(
                "/api/payments/success",
                json={"payment_id": 10, "user_id": 9, "provider_payment_id": "chg_1", "amount": 100},
                headers=headers,
            )
    finally:
        app.state.dishka_container = _prev
        await _container.close()
    assert len([x for x in fake_session.added if isinstance(x, AnalyticsEvent)]) == 1
