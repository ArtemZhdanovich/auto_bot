"""Token Bucket для Telegram API (ТЗ п. 12.6): ~30 msg/s глобально,
~1 msg/s на чат. Счётчики — в Redis (Lua, атомарно).

Принципы:
- fail-open: Redis недоступен → отправка НЕ блокируется (0.0), 429 с
  retry_after обрабатывается в send_telegram_message как страховка;
- исчерпание локальных слотов → короткий sleep и повтор (до max_attempts),
  затем отправка всё равно — лимитеры сглаживают, а не запрещают.
"""

import asyncio
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

# KEYS[1] — hash-ключ бакета; ARGV: capacity, refill_per_sec, now_ms, cost, ttl_ms
_LUA = """
local state = redis.call('hmget', KEYS[1], 'tokens', 'ts')
local tokens = tonumber(state[1])
local ts = tonumber(state[2])
local now = tonumber(ARGV[3])
if tokens == nil then tokens = tonumber(ARGV[1]) end
if ts == nil then ts = now end
local delta = now - ts
if delta < 0 then delta = 0 end
tokens = math.min(tonumber(ARGV[1]), tokens + delta / 1000.0 * tonumber(ARGV[2]))
local allowed, retry_after_ms = 1, 0
local cost = tonumber(ARGV[4])
if tokens < cost then
  allowed = 0
  retry_after_ms = math.ceil((cost - tokens) / tonumber(ARGV[2]) * 1000.0)
else
  tokens = tokens - cost
end
redis.call('hset', KEYS[1], 'tokens', tokens, 'ts', now)
redis.call('pexpire', KEYS[1], tonumber(ARGV[5]))
return {allowed, tostring(tokens), retry_after_ms}
"""



def bucket_advance(
    tokens: float,
    ts_ms: float,
    now_ms: float,
    capacity: float,
    refill_per_sec: float,
    cost: float,
) -> tuple[bool, float, float]:
    """Чистая логика бакета → (allowed, new_tokens, retry_after_sec).

    Наполнение пропорционально прошедшему времени с капчей на capacity;
    при нехватке — retry_after = дефицит / скорость наполнения.
    """
    delta = max(0.0, now_ms - ts_ms)
    tokens = min(capacity, tokens + delta / 1000.0 * refill_per_sec)
    if tokens < cost:
        wait = (cost - tokens) / refill_per_sec if refill_per_sec > 0 else 1e9
        return False, tokens, wait
    return True, tokens - cost, 0.0


class TokenBucket:
    """Один Redis-бакет. Ошибка Redis → 0.0 (fail-open, не блокируем отправку)."""

    def __init__(
        self,
        redis: Any,
        key: str,
        capacity: float,
        refill_per_sec: float,
        max_wait_sec: float = 10.0,
        ttl_sec: int = 3600,
    ) -> None:
        self.redis = redis
        self.key = key
        self.capacity = capacity
        self.refill_per_sec = refill_per_sec
        self.max_wait_sec = max_wait_sec
        self.ttl_sec = ttl_sec

    async def try_acquire(self, cost: float = 1.0) -> float:
        """Резервирует cost токенов; 0.0 = можно, иначе сколько ждать (сек)."""
        if self.redis is None or self.capacity <= 0 or self.refill_per_sec <= 0:
            return 0.0  # выключен или нет Redis — не ограничиваем
        now_ms = int(time.time() * 1000)
        try:
            res = await self.redis.eval(
                _LUA,
                1,
                self.key,
                self.capacity,
                self.refill_per_sec,
                now_ms,
                cost,
                self.ttl_sec * 1000,
            )
            allowed = int(res[0]) == 1
            retry_ms = float(res[2])
        except Exception as exc:  # noqa: BLE001 — лимитер не ломает рассылку
            logger.warning("rate_limit_redis_failed: %s", exc)
            return 0.0
        if allowed:
            return 0.0
        return min(retry_ms / 1000.0, self.max_wait_sec)


class TelegramRateLimiter:
    """Глобальный (~30 msg/s) + пер-чатовый (~1 msg/s) лимитеры отправки."""

    def __init__(
        self,
        redis: Any,
        global_per_sec: float = 30.0,
        chat_per_sec: float = 1.0,
        max_wait_sec: float = 10.0,
    ) -> None:
        self.max_wait_sec = max_wait_sec
        self._global = (
            TokenBucket(redis, "rl:tg:global", global_per_sec, global_per_sec, max_wait_sec)
            if global_per_sec > 0
            else None
        )
        # Пер-чат: capacity 1 — очередь на чат не копим («не чаще 1 msg/s»)
        self._chat_per_sec = chat_per_sec
        self._redis = redis
        self._chat: dict[int, TokenBucket] = {}

    def _chat_bucket(self, chat_id: int) -> TokenBucket | None:
        if self._chat_per_sec <= 0:
            return None
        if chat_id not in self._chat:  # размер ограничен TTL бакетов в Redis
            self._chat[chat_id] = TokenBucket(
                self._redis,
                f"rl:tg:chat:{chat_id}",
                1.0,
                self._chat_per_sec,
                self.max_wait_sec,
            )
        return self._chat[chat_id]

    async def wait_slot(self, chat_id: int, max_attempts: int = 3) -> float:
        """Дождаться слотов глобально и для чата. Возвращает суммарное время
        ожидания (0 = отправляли сразу). После max_attempts шлём всё равно.
        """
        waited = 0.0
        for _ in range(max_attempts):
            wait = 0.0
            if self._global is not None:
                wait = max(wait, await self._global.try_acquire())
            chat = self._chat_bucket(chat_id)
            if chat is not None:
                wait = max(wait, await chat.try_acquire())
            if wait <= 0:
                return waited
            await asyncio.sleep(wait)
            waited += wait
        return waited


_limiter: TelegramRateLimiter | None = None


def init_rate_limiter(redis: Any, global_per_sec: float, chat_per_sec: float) -> None:
    """Инициализация на старте send-воркера."""
    global _limiter
    _limiter = TelegramRateLimiter(redis, global_per_sec, chat_per_sec)


def reset_rate_limiter() -> None:
    global _limiter
    _limiter = None


def set_rate_limiter(limiter: TelegramRateLimiter | None) -> None:
    """Прямая подмена (тесты)."""
    global _limiter
    _limiter = limiter


async def rate_limit_wait(chat_id: int, limiter: "TelegramRateLimiter | None" = None) -> float:
    """Точка входа для send_telegram_message.

    `limiter` — точка инъекции (тесты, DI); без неё используется
    модульный синглтон `_limiter` (обратная совместимость).
    Без лимитера — 0.0 (no-op).
    """
    active = limiter if limiter is not None else _limiter
    if active is None:
        return 0.0
    return await active.wait_slot(chat_id)