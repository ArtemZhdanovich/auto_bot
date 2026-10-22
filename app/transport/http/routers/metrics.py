"""Системные метрики мониторинга (этап 8, ТЗ п. 10): GET /metrics.

Роутер переиспользуется дважды без копирования кода:
- монолит `app/main.py` (профиль mono);
- доменный сервис `services/admin` (профиль micro) — gateway проксирует
  `/metrics` в admin, т.к. сам маршрутизирует только `/api/*`.

Сбор данных — в `GetMetricsSnapshot` и репозитории метрик; здесь только
кэш ответа, проверка доступности БД и сериализация в контракт.

Кэш: Redis 30s — Prometheus скрэйпит /metrics каждые 15s, но COUNT(*) на
больших таблицах дешевле выполнять раз в 30s. Кэш общий на инстанс.

`_probe_db` остаётся точкой проверки БД: одна сломанная метрика не валит
эндпоинт (200 с `None`), но недоступная БД — 503.
"""


from __future__ import annotations
from app.transport.http.deps.dishka import InternalDishkaRoute

import logging

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.application.metrics.use_cases import (
    GetMetricsSnapshot,
    MetricsSnapshot,
)
from app.transport.http.deps.internal import RedisDep
from app.transport.http.deps.use_cases import MetricsSnapshotDep

logger = logging.getLogger(__name__)

router = APIRouter(route_class=InternalDishkaRoute, tags=["system"])

_METRICS_CACHE_KEY = "metrics:snapshot:v1"
_METRICS_CACHE_TTL_SEC = 30


# ---------------------------------------------------------------------------
# Схемы ответа (контракт для клиентов)
# ---------------------------------------------------------------------------


class UsersMetrics(BaseModel):
    total: int | None = None
    new_24h: int | None = None
    by_tier: dict[str, int] = Field(default_factory=dict)


class CountMetrics(BaseModel):
    total: int | None = None
    new_24h: int | None = None


class SourceMetrics(BaseModel):
    source_name: str
    avg_latency_ms: int | None = None
    error_streak: int | None = None
    last_success_at: str | None = None
    last_error_at: str | None = None


class MetricsOut(BaseModel):
    generated_at: str
    users: UsersMetrics
    filters: CountMetrics
    listings: CountMetrics
    leads: CountMetrics
    price_analyses: CountMetrics
    notifications_sent_24h: int | None = None
    sources: list[SourceMetrics] = Field(default_factory=list)
    events_24h: dict[str, int] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Вспомогательные
# ---------------------------------------------------------------------------


async def _probe_db(use_case: GetMetricsSnapshot) -> bool:
    """`True`, если БД отвечает. `False` — недоступна (роутер отдаёт 503).

    Отдельная функция — точка подмены в тестах на недоступную БД.
    """
    return await use_case.db_available()


def _to_response(snapshot: MetricsSnapshot) -> MetricsOut:
    """Снимок сценария → контракт ответа."""
    return MetricsOut(
        generated_at=snapshot.generated_at.isoformat(),
        users=UsersMetrics(
            total=snapshot.users_total,
            new_24h=snapshot.users_new_24h,
            by_tier=snapshot.users_by_tier,
        ),
        filters=CountMetrics(total=snapshot.filters.total),
        listings=CountMetrics(
            total=snapshot.listings.total,
            new_24h=snapshot.listings.new_24h,
        ),
        leads=CountMetrics(
            total=snapshot.leads.total,
            new_24h=snapshot.leads.new_24h,
        ),
        price_analyses=CountMetrics(
            total=snapshot.price_analyses.total,
            new_24h=snapshot.price_analyses.new_24h,
        ),
        notifications_sent_24h=snapshot.notifications_sent_24h,
        sources=[
            SourceMetrics(
                source_name=item.source_name,
                avg_latency_ms=item.avg_latency_ms,
                error_streak=item.error_streak,
                last_success_at=item.last_success_at,
                last_error_at=item.last_error_at,
            )
            for item in snapshot.sources
        ],
        events_24h=snapshot.events_24h,
    )


# ---------------------------------------------------------------------------
# Эндпоинт
# ---------------------------------------------------------------------------


@router.get(
    "/metrics",
    response_model=MetricsOut,
)
async def metrics(use_case: MetricsSnapshotDep, redis: RedisDep) -> MetricsOut:
    """Базовые метрики для мониторинга (этап 8).

    Кэш 30s в Redis. При недоступной БД — 503 (иначе «мониторинг зелёный,
    прод мёртв»).
    """
    # Кэш: Prometheus скрэйпит каждые 15s, COUNT(*) на больших таблицах —
    # дорого. TTL 30s покрывает 2 скрэйпа.
    try:
        cached_raw = await redis.get(_METRICS_CACHE_KEY)
        if cached_raw is not None:
            return MetricsOut.model_validate_json(cached_raw)
    except Exception:  # noqa: BLE001 — кэш не критичен
        logger.warning("metrics_cache_get_failed", exc_info=True)

    if not await _probe_db(use_case):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database is unavailable",
        )

    out = _to_response(await use_case.execute())

    try:
        await redis.setex(
            _METRICS_CACHE_KEY, _METRICS_CACHE_TTL_SEC, out.model_dump_json()
        )
    except Exception:  # noqa: BLE001 — кэш не критичен
        logger.warning("metrics_cache_set_failed", exc_info=True)

    return out


__all__ = ["router"]
