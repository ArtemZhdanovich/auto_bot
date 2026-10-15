"""Антифлуд бота: FloodGate (скользящее окно) и FloodMiddleware (дроп + варн)."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.bot.throttle import FLOOD_TEXT, FloodGate, FloodMiddleware


def test_gate_allows_within_limit():
    g = FloodGate(max_events=3, window_sec=10)
    assert g.allow(1, now=0) and g.allow(1, now=1) and g.allow(1, now=2)


def test_gate_blocks_over_limit_same_window():
    g = FloodGate(max_events=3, window_sec=10)
    for t in (0, 1, 2):
        g.allow(1, now=t)
    assert not g.allow(1, now=3)


def test_gate_sliding_window_expires():
    g = FloodGate(max_events=2, window_sec=10)
    g.allow(1, now=0)
    g.allow(1, now=1)
    assert not g.allow(1, now=5)  # окно ещё полное
    assert g.allow(1, now=12)  # старые события вышли из окна


def test_gate_users_are_independent():
    g = FloodGate(max_events=1, window_sec=10)
    assert g.allow(1, now=0)
    assert g.allow(2, now=0)  # у другого юзера свой лимит
    assert not g.allow(1, now=1)


def test_should_warn_once_per_window():
    g = FloodGate(window_sec=10)
    assert g.should_warn(1, now=0)
    assert not g.should_warn(1, now=5)  # в том же окне — молчим
    assert g.should_warn(1, now=11)  # новое окно — можно снова


async def _run(coro):
    return await coro


def test_middleware_passes_normal_user():
    mw = FloodMiddleware(FloodGate(max_events=2, window_sec=10))
    handler = AsyncMock(return_value="ok")
    event = SimpleNamespace(from_user=SimpleNamespace(id=7), answer=AsyncMock())
    import asyncio

    assert asyncio.run(mw(handler, event, {})) == "ok"
    handler.assert_awaited_once()
    event.answer.assert_not_called()


def test_middleware_drops_flooder_and_warns_once():
    import asyncio

    gate = FloodGate(max_events=1, window_sec=10)
    mw = FloodMiddleware(gate)
    handler = AsyncMock(return_value="ok")
    event = SimpleNamespace(from_user=SimpleNamespace(id=7), answer=AsyncMock())

    assert asyncio.run(mw(handler, event, {})) == "ok"  # первое — ок
    # второе — дроп + предупреждение
    assert asyncio.run(mw(handler, event, {})) is None
    handler.assert_awaited_once()
    event.answer.assert_awaited_once_with(FLOOD_TEXT)
    # третье — дроп без повторного варна
    event.answer.reset_mock()
    assert asyncio.run(mw(handler, event, {})) is None
    event.answer.assert_not_called()


def test_middleware_event_from_user_data_priority():
    """event_from_user из data (aiogram) важнее event.from_user."""
    import asyncio

    mw = FloodMiddleware(FloodGate(max_events=1, window_sec=10))
    handler = AsyncMock(return_value="ok")
    event = SimpleNamespace(from_user=SimpleNamespace(id=1))
    data = {"event_from_user": SimpleNamespace(id=2)}
    assert asyncio.run(mw(handler, event, data)) == "ok"
    # id=2 исчерпан, id=1 — нет
    assert asyncio.run(mw(handler, event, data)) is None
    other = SimpleNamespace(from_user=SimpleNamespace(id=1))
    assert asyncio.run(mw(handler, other, {})) == "ok"
