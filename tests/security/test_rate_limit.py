"""Тесты Token Bucket Telegram (ТЗ п. 12.6, кандидат №1 P2-бэклога):
чистая логика наполнения, Redis-бакет (Lua-контракт), fail-open, wait_slot.
"""

from typing import Any

import pytest

from app.infrastructure.services import rate_limit
from app.infrastructure.services.rate_limit import (
    TokenBucket,
    TelegramRateLimiter,
    bucket_advance,
)


# ---------- чистая логика ----------


def test_bucket_full_capacity_first_cost_allowed():
    allowed, tokens, wait = bucket_advance(30, 0, 0, 30, 30, 1)
    assert allowed and tokens == 29 and wait == 0


def test_bucket_refills_over_time():
    # 0.5 сек при refill 30/s → +15 токенов (с капчей)
    allowed, tokens, _ = bucket_advance(0, 0, 500, 30, 30, 1)
    assert allowed and tokens == 14


def test_bucket_capacity_cap():
    # Полный бакет + время → не выше capacity
    _, tokens, _ = bucket_advance(30, 0, 60_000, 30, 30, 1)
    assert tokens == 29


def test_bucket_denied_returns_retry_after():
    allowed, tokens, wait = bucket_advance(0.2, 0, 0, 30, 10, 1)
    assert not allowed and tokens == 0.2
    assert wait == pytest.approx((1 - 0.2) / 10)


def test_bucket_negative_time_delta_ignored():
    # Откат часов не должен «наполнять» бакет
    allowed, tokens, _ = bucket_advance(0, 1000, 500, 30, 30, 1)
    assert not allowed and tokens == 0


# ---------- Redis-бакет (контракт Lua-ответа) ----------


class _ScriptedRedis:
    """eval возвращает заготовленные ответы [allowed, tokens, retry_ms]."""

    def __init__(self, results: list[list[Any]]) -> None:
        self.results = results
        self.calls: list[Any] = []

    async def eval(self, *args: Any) -> list[Any]:
        self.calls.append(args)
        return self.results.pop(0)


class _BrokenRedis:
    async def eval(self, *args: Any) -> list[Any]:
        raise ConnectionError("redis down")


async def test_token_bucket_allowed():
    redis = _ScriptedRedis([[1, "29", 0]])
    bucket = TokenBucket(redis, "rl:tg:global", 30, 30)
    assert await bucket.try_acquire() == 0.0
    assert len(redis.calls) == 1


async def test_token_bucket_denied_returns_wait():
    redis = _ScriptedRedis([[0, "0.5", 800]])
    bucket = TokenBucket(redis, "rl:tg:global", 30, 30)
    assert await bucket.try_acquire() == pytest.approx(0.8)


async def test_token_bucket_fail_open_on_redis_error():
    bucket = TokenBucket(_BrokenRedis(), "rl:tg:global", 30, 30)
    assert await bucket.try_acquire() == 0.0  # Redis лежит → не блокируем


async def test_token_bucket_disabled_rate():
    bucket = TokenBucket(None, "rl:tg:global", 0, 0)
    assert await bucket.try_acquire() == 0.0


async def test_token_bucket_retry_capped_by_max_wait():
    redis = _ScriptedRedis([[0, "0", 500_000]])  # 500 сек
    bucket = TokenBucket(redis, "rl:tg:global", 30, 30, max_wait_sec=10)
    assert await bucket.try_acquire() == 10.0


# ---------- TelegramRateLimiter ----------


class _StubLimiter(TelegramRateLimiter):
    """С глобальными/чатовыми бакетами на скриптованном Redis."""

    def __init__(self, redis: Any) -> None:
        super().__init__(redis, global_per_sec=30, chat_per_sec=1, max_wait_sec=5)


async def test_wait_slot_zero_when_all_buckets_allow(monkeypatch):
    redis = _ScriptedRedis([[1, "29", 0], [1, "0", 0]])
    limiter = _StubLimiter(redis)
    # asyncio.sleep не должен вызываться
    monkeypatch.setattr(
        rate_limit.asyncio, "sleep", _fail_sleep
    )
    assert await limiter.wait_slot(42) == 0.0


async def _fail_sleep(*_a: Any) -> None:
    raise AssertionError("sleep не должен вызываться при свободных слотах")


async def test_wait_slot_sleeps_when_chat_denied(monkeypatch):
    # 1-й проход: chat deny (0.4s) → sleep → 2-й проход: оба allow
    redis = _ScriptedRedis(
        [[1, "29", 0], [0, "0", 400], [1, "29", 0], [1, "0", 0]]
    )
    limiter = _StubLimiter(redis)
    slept: list[float] = []

    async def _fake_sleep(sec: float) -> None:
        slept.append(sec)

    monkeypatch.setattr(rate_limit.asyncio, "sleep", _fake_sleep)
    waited = await limiter.wait_slot(42)
    assert slept == [0.4] and waited == pytest.approx(0.4)


async def test_wait_slot_disabled_limiter():
    limiter = TelegramRateLimiter(None, global_per_sec=0, chat_per_sec=0)
    assert await limiter.wait_slot(1) == 0.0


# ---------- интеграция с send_telegram_message ----------


async def test_send_message_uses_rate_limit_wait(monkeypatch):
    import app.infrastructure.workers.send as send_mod

    waited_calls: list[int] = []

    async def _fake_wait(chat_id: int) -> float:
        waited_calls.append(chat_id)
        return 0.0

    monkeypatch.setattr(send_mod.rate_limit, "rate_limit_wait", _fake_wait)
    ok, err = await send_mod.send_telegram_message("", 1, "hi")
    assert ok is False and err == "no_bot_token"
    assert waited_calls == []  # нет токена — до лимитера не доходим

    async def _post(*a: Any, **kw: Any) -> Any:
        class _Resp:
            status_code = 200

            def json(self) -> dict:
                return {}

        return _Resp()

    class _Client:
        def __init__(self, *a: Any, **kw: Any) -> None:
            pass

        async def __aenter__(self) -> "_Client":
            return self

        async def __aexit__(self, *exc: object) -> bool:
            return False

        async def post(self, *a: Any, **kw: Any) -> Any:
            return await _post()

    monkeypatch.setattr(send_mod.httpx, "AsyncClient", _Client)
    ok, err = await send_mod.send_telegram_message("T", 7, "hi")
    assert ok is True and err is None
    assert waited_calls == [7]  # лимитер применён с chat_id


async def test_global_limiter_registered(monkeypatch):
    rate_limit.reset_rate_limiter()
    rate_limit.init_rate_limiter(None, 30, 1)
    assert rate_limit._limiter is not None  # noqa: SLF001
    assert await rate_limit.rate_limit_wait(1) == 0.0  # redis None → fail-open
    rate_limit.reset_rate_limiter()
    assert await rate_limit.rate_limit_wait(1) == 0.0