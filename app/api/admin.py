"""Admin-эндпоинты: управление источниками (ТЗ п. 4.7, 6.4).

Полная admin-панель — на следующих этапах; здесь минимум этапа 2:
CRUD источников с валидацией adapter_class по ADAPTER_CLASSES
и просмотр здоровья источников.
"""

from fastapi import APIRouter, Header, HTTPException, status
from sqlalchemy import select

from app.adapters import ADAPTER_CLASSES
from app.core.deps import SessionDep
from app.queue import enqueue_task
from app.core.deps_internal import check_internal_token
from app.db.models import Source, SourceHealth
from app.schemas.criteria import SourceConfig

router = APIRouter(prefix="/api/admin", tags=["admin"])

InternalToken = Header(default=None, alias="X-Internal-Token")


@router.get("/adapters")
async def list_adapters(x_internal_token: str | None = InternalToken) -> dict[str, list[str]]:
    """Доступные классы адаптеров (для валидации при создании источника)."""
    check_internal_token(x_internal_token)
    return {"adapters": sorted(ADAPTER_CLASSES)}


@router.get("/sources")
async def list_sources(
    session: SessionDep,
    x_internal_token: str | None = InternalToken,
) -> list[SourceConfig]:
    check_internal_token(x_internal_token)
    rows = (await session.execute(select(Source).order_by(Source.priority))).scalars().all()
    return [SourceConfig.model_validate(row, from_attributes=True) for row in rows]


@router.put("/sources/{name}", response_model=SourceConfig)
async def upsert_source(
    name: str,
    body: SourceConfig,
    session: SessionDep,
    x_internal_token: str | None = InternalToken,
) -> SourceConfig:
    """Создать/обновить источник. adapter_class обязан быть в ADAPTER_CLASSES."""
    check_internal_token(x_internal_token)
    if body.name != name:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "name mismatch")
    if body.adapter_class not in ADAPTER_CLASSES:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"adapter_class must be one of: {sorted(ADAPTER_CLASSES)}",
        )

    source = await session.get(Source, name)
    if source is None:
        source = Source(name=name)
        session.add(source)
    for field in (
        "adapter_class", "kind", "enabled", "priority", "schedule_cron",
        "rate_limit_per_min", "max_concurrency", "timeout_sec",
        "retry_policy", "credentials_ref", "capabilities", "config",
    ):
        setattr(source, field, getattr(body, field))
    await session.commit()
    await session.refresh(source)
    return SourceConfig.model_validate(source, from_attributes=True)


@router.get("/sources/{name}/health")
async def source_health(
    name: str,
    session: SessionDep,
    x_internal_token: str | None = InternalToken,
) -> dict:
    check_internal_token(x_internal_token)
    health = await session.get(SourceHealth, name)
    if health is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no health data")
    return {
        "source_name": health.source_name,
        "last_success_at": health.last_success_at,
        "last_error_at": health.last_error_at,
        "error_streak": health.error_streak,
        "last_error": health.last_error,
        "avg_latency_ms": health.avg_latency_ms,
        "total_fetched": health.total_fetched,
    }


@router.post("/sources/{name}/fetch")
async def trigger_fetch(
    name: str,
    session: SessionDep,
    x_internal_token: str | None = InternalToken,
    criteria: dict | None = None,
    max_pages: int = 1,
) -> dict:
    """Поставить задачу парсинга источника в RabbitMQ (очередь `fetch`)."""
    check_internal_token(x_internal_token)
    source = await session.get(Source, name)
    if source is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"source {name} not found")

    message_id = await enqueue_task("fetch_source_task", name, criteria, max_pages)
    return {"queued": True, "job_id": message_id}

