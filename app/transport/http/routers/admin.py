"""Admin-эндпоинты: источники, лиды, ассистент, воронки (ТЗ п. 4.7, 6.4).

Тонкий контроллер: разбор параметров, вызов сценария и маппинг ошибок в
HTTP-коды. SQL, коммиты, очередь и storage живут в сценариях и репозиториях,
поэтому здесь нет ни `SessionDep`, ни импортов infrastructure/domain.

Безопасность (сохранена в сценариях и DTO):
- все эндпоинты защищены router-level `Depends(check_internal_token)`;
- `config.base_url` валидируется: http(s), не private/loopback/link-local;
- `credentials_ref` должен начинаться с разрешённого префикса;
- `trigger_fetch.criteria` валидируется как SearchCriteria;
- `assistant_chat` не отдаёт клиенту причину LLM-сбоя.
"""


from __future__ import annotations
from app.transport.http.deps.dishka import DishkaRoute

import logging
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from pydantic import BaseModel, Field

from app.application.admin.use_cases import (
    AssistantQuery,
    InvalidSourceConfig,
    LeadsQuery,
    LLMUnavailable,
    QueueUnavailable,
    RawPayloadNotFound,
    SourceConflict,
    SourceNotFound,
    StorageUnavailable,
    TriggerFetchCommand,
    UpsertSourceCommand,
)
from app.application.dto.admin import (
    SOURCE_NAME_PATTERN,
    SourceConfigDTO,
    is_safe_external_url,
)
from app.config import get_settings
from app.transport.http.deps.internal import check_internal_token
from app.transport.http.deps.use_cases import (
    AskAssistantDep,
    GetFunnelDep,
    GetListingRawPayloadDep,
    GetRetentionDep,
    GetSourceHealthDep,
    ListAdapterClassesDep,
    ListLeadsDep,
    ListSourcesDep,
    TriggerFetchDep,
    UpsertSourceDep,
)
from app.infrastructure.ai.llm_client import get_cached_llm_client as _cached_llm_client
from app.infrastructure.messaging.legacy_worker import enqueue_task

logger = logging.getLogger(__name__)

# Router-level auth: ни один эндпоинт не может «забыть» проверку токена.
router = APIRouter(
        route_class=DishkaRoute,
    prefix="/api/admin",
    tags=["admin"],
    dependencies=[Depends(check_internal_token)],
)

SourceName = Annotated[str, Path(pattern=SOURCE_NAME_PATTERN)]


def _is_safe_external_url(url: str) -> bool:
    """Совместимость: SSRF-проверка живёт в DTO админки.

    Оставлено публичным именем модуля, потому что проверка исторически
    вызывалась отсюда и на неё опираются тесты.
    """
    return is_safe_external_url(url)


# --- adapters / leads / sources --------------------------------------


@router.get("/adapters")
async def list_adapters(use_case: ListAdapterClassesDep) -> dict[str, list[str]]:
    """Доступные классы адаптеров (валидация при создании источника)."""
    return {"adapters": await use_case.execute()}


@router.get("/leads")
async def admin_leads(
    use_case: ListLeadsDep,
    lead_status: str | None = Query(default=None, alias="status"),
    partner_id: int | None = Query(default=None, ge=1),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[dict]:
    """Лиды для админ-панели (read-only, UI/UX ТЗ §3.6)."""
    return await use_case.execute(
        LeadsQuery(
            status=lead_status,
            partner_id=partner_id,
            limit=limit,
            offset=offset,
        )
    )


@router.get("/sources")
async def list_sources(use_case: ListSourcesDep) -> list[SourceConfigDTO]:
    """Все источники (включая выключенные)."""
    return await use_case.execute()


@router.put("/sources/{name}")
async def upsert_source(
    name: SourceName,
    body: SourceConfigDTO,
    use_case: UpsertSourceDep,
) -> SourceConfigDTO:
    """Создать/обновить источник. adapter_class обязан быть в ADAPTER_CLASSES."""
    try:
        return await use_case.execute(
            UpsertSourceCommand(name=name, config=body)
        )
    except InvalidSourceConfig as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)
        ) from exc
    except SourceConflict as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


@router.get("/sources/{name}/health")
async def source_health(
    use_case: GetSourceHealthDep,
    name: SourceName,
) -> dict:
    """Здоровье источника: последние успехи/ошибки, latency."""
    try:
        return await use_case.execute(name)
    except SourceNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.post("/sources/{name}/fetch")
async def trigger_fetch(
    use_case: TriggerFetchDep,
    name: SourceName,
    criteria: dict[str, Any] | None = None,
    max_pages: int = Query(default=1, ge=1, le=50),
) -> dict:
    """Поставить задачу парсинга источника в очередь (`fetch`).

    Returns:
        `{"queued": True, "job_id": <message_id>}`.

    Raises:
        HTTPException: 404 если источника нет; 422 если критерии невалидны;
            503 если очередь недоступна.
    """
    try:
        job_id = await use_case.execute(
            TriggerFetchCommand(
                source_name=name,
                criteria=criteria,
                max_pages=max_pages,
                enqueue=enqueue_task,
            )
        )
    except SourceNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except InvalidSourceConfig as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)
        ) from exc
    except QueueUnavailable as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)
        ) from exc

    logger.warning("admin_fetch_queued source=%s job_id=%s", name, job_id)
    return {"queued": True, "job_id": job_id}


# --- AI-помощник (UI/UX ТЗ §3.2, фаза 4) -----------------------------


class AssistantTurn(BaseModel):
    """Один ход в истории админ-чата."""

    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4000)


class AssistantIn(BaseModel):
    """Запрос к AI-помощнику админа."""

    message: str = Field(min_length=1, max_length=4000)
    history: list[AssistantTurn] = Field(default_factory=list, max_length=20)


@router.post("/assistant/chat")
async def assistant_chat(body: AssistantIn, use_case: AskAssistantDep) -> dict:
    """Один ход AI-помощника админа: LLM с tools (leads/partners/sources)."""
    settings = get_settings()
    try:
        reply = await use_case.execute(
            AssistantQuery(
                message=body.message,
                history=[turn.model_dump() for turn in body.history],
                llm_factory=lambda: _cached_llm_client(
                    settings.llm_provider, settings.llm_default_model
                ),
            )
        )
    except LLMUnavailable as exc:
        # Детали сбоя — только в лог, клиенту generic-ответ.
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)
        ) from exc
    return {"reply": reply}


@router.get("/listings/{listing_id}/raw")
async def admin_listing_raw(
    use_case: GetListingRawPayloadDep,
    listing_id: int = Path(ge=1),
) -> dict:
    """Presigned URL на сырой payload объявления (PLAN.md, фаза 6).

    Прямой доступ к MinIO админке не даём — короткий presigned GET
    (RAW_PRESIGN_TTL_SEC). raw_key = NULL → 404.
    """
    try:
        return await use_case.execute(listing_id)
    except RawPayloadNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except StorageUnavailable as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, str(exc)
        ) from exc


# --- аналитика (P2-бэклог: SQL-дашборды) -----------------------------


@router.get("/analytics/funnel")
async def analytics_funnel(
    use_case: GetFunnelDep,
    days: int = Query(default=30, ge=1, le=365),
) -> dict:
    """Воронка search→dialog→lead→subscription."""
    return {"days": days, "stages": await use_case.execute(days)}


@router.get("/analytics/retention")
async def analytics_retention(
    use_case: GetRetentionDep,
    days: int = Query(default=30, ge=1, le=365),
) -> dict:
    """Когорты D1/D7 по дню регистрации."""
    return {"days": days, "cohorts": await use_case.execute(days)}


__all__ = ["router"]
