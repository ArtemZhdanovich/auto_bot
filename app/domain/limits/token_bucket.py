"""Чистая математика token bucket (без Redis)."""

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
