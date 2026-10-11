"""Admin-эндпоинты: управление источниками (ТЗ п. 4.7, 6.4).

Полная admin-панель — на следующих этапах; здесь минимум этапа 2:
CRUD источников с валидацией adapter_class по ADAPTER_CLASSES
и просмотр здоровья источников.
"""

from fastapi import APIRouter, Header, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.adapters import ADAPTER_CLASSES
from app.ai.llm_client import LLMError, build_llm_client
from app.core.config import get_settings
from app.core.deps import SessionDep
from app.queue import enqueue_task
from app.core.deps_internal import check_internal_token
from app.db.models import Lead, Listing, Source, SourceHealth
from app.schemas.criteria import SourceConfig
from app.services.admin_assistant import assistant_reply
from app.services import funnels
from app.services.storage import storage_from_settings

router = APIRouter(prefix="/api/admin", tags=["admin"])

InternalToken = Header(default=None, alias="X-Internal-Token")


@router.get("/adapters")
async def list_adapters(x_internal_token: str | None = InternalToken) -> dict[str, list[str]]:
    """Доступные классы адаптеров (для валидации при создании источника)."""
    check_internal_token(x_internal_token)
    return {"adapters": sorted(ADAPTER_CLASSES)}


@router.get("/leads")
async def admin_leads(
    session: SessionDep,
    x_internal_token: str | None = InternalToken,
    lead_status: str | None = Query(default=None, alias="status"),
    partner_id: int | None = Query(default=None, ge=1),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[dict]:
    """Лиды для админ-панели (read-only, UI/UX ТЗ §3.6)."""
    check_internal_token(x_internal_token)
    conditions = []
    if lead_status:
        conditions.append(Lead.status == lead_status)
    if partner_id is not None:
        conditions.append(Lead.partner_id == partner_id)
    query = select(Lead).order_by(Lead.id.desc()).limit(limit).offset(offset)
    if conditions:
        query = query.where(*conditions)
    rows = (await session.execute(query)).scalars().all()
    return [
        {
            "id": lead.id,
            "user_id": lead.user_id,
            "listing_id": lead.listing_id,
            "source": lead.source,
            "status": lead.status,
            "partner_id": lead.partner_id,
            "partner_status": lead.partner_status,
            "comment": lead.comment,
            "qualification": lead.qualification,
            "created_at": lead.created_at,
        }
        for lead in rows
    ]


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


# --- AI-помощник (UI/UX ТЗ §3.2, фаза 4) --------------------------------------


class AssistantTurn(BaseModel):
    role: str  # user | assistant
    content: str


class AssistantIn(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    history: list[AssistantTurn] = Field(default_factory=list, max_length=20)


@router.post("/assistant/chat")
async def assistant_chat(
    body: AssistantIn,
    session: SessionDep,
    x_internal_token: str | None = InternalToken,
) -> dict:
    """Один ход AI-помощника админа: LLM с tools (leads/partners/sources)."""
    check_internal_token(x_internal_token)
    try:
        llm = build_llm_client(get_settings())
    except LLMError as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, f"LLM unavailable: {exc}"
        ) from exc
    try:
        reply = await assistant_reply(
            session, llm, body.message, [t.model_dump() for t in body.history]
        )
    finally:
        aclose = getattr(llm, "aclose", None)
        if aclose is not None:
            await aclose()
    return {"reply": reply}


@router.get("/listings/{listing_id}/raw")
async def admin_listing_raw(
    listing_id: int,
    session: SessionDep,
    x_internal_token: str | None = InternalToken,
) -> dict:
    """Presigned URL на сырой payload объявления (PLAN.md, фаза 6).

    Прямой доступ к MinIO админке не даём — короткий presigned GET
    (RAW_PRESIGN_TTL_SEC, по умолчанию 10 мин). raw_key = NULL (MinIO
    был недоступен при парсинге или флаг выключен) → 404.
    """
    check_internal_token(x_internal_token)
    listing = (
        await session.execute(select(Listing).where(Listing.id == listing_id))
    ).scalar_one_or_none()
    if listing is None or not listing.raw_key:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "raw payload not found in storage"
        )
    settings = get_settings()
    url = await storage_from_settings().presign_raw(
        listing.raw_key, settings.raw_presign_ttl_sec
    )
    if url is None:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, "storage unavailable"
        )
    return {
        "key": listing.raw_key,
        "url": url,
        "expires_in": settings.raw_presign_ttl_sec,
    }


@router.get("/analytics/funnel")
async def analytics_funnel(
    session: SessionDep,
    x_internal_token: str | None = InternalToken,
    days: int = Query(default=30, ge=1, le=365),
) -> dict:
    """Воронка search→dialog→lead→subscription (P2-бэклог: SQL-дашборды)."""
    check_internal_token(x_internal_token)
    stages = await funnels.funnel(session, days)
    return {"days": days, "stages": stages}


@router.get("/analytics/retention")
async def analytics_retention(
    session: SessionDep,
    x_internal_token: str | None = InternalToken,
    days: int = Query(default=30, ge=1, le=365),
) -> dict:
    """Когорты D1/D7 по дню регистрации (P2-бэклог: SQL-дашборды)."""
    check_internal_token(x_internal_token)
    cohorts = await funnels.retention(session, days)
    return {"days": days, "cohorts": cohorts}

