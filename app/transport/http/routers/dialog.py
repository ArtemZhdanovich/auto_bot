"""Диалоговый API для бота (internal-токен) — тонкий контроллер.

Схемы запросов/ответов, вызов сценария и маппинг прикладных ошибок в HTTP.
Ни SQL, ни Redis, ни фиксации транзакций: состояние диалога, архив и
фильтры живут за портами `DialogStorePort`/`DialogRepositoryPort` в
`app.application.dialog.use_cases`.
"""


from __future__ import annotations
from app.transport.http.deps.dishka import InternalDishkaRoute

import logging
from typing import Any, NoReturn

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.application.dto.dialog import (
    DialogFinalizeCommand,
    DialogMessageCommand,
    DialogReply,
    DialogRestoreResult,
)
from app.application.errors import AppError, Forbidden, InvalidInput, NotFound
from app.application.dialog.use_cases import AiUnavailable
from app.transport.http.deps.use_cases import (
    FinalizeDialogDep,
    ResetDialogDep,
    RestoreDialogDep,
    SendDialogMessageDep,
)

logger = logging.getLogger(__name__)

router = APIRouter(route_class=InternalDishkaRoute,
    prefix="/api/dialog",
    tags=["dialog"],
)

_TEXT_MAX_LEN = 4000


# ---------------------------------------------------------------------------
# Схемы запросов/ответов
# ---------------------------------------------------------------------------


class DialogMessageRequest(BaseModel):
    """Запрос на один ход диалога."""

    user_id: int = Field(gt=0, description="telegram_id пользователя (>0)")
    text: str = Field(min_length=1, max_length=_TEXT_MAX_LEN)


class DialogResetRequest(BaseModel):
    """Запрос на сброс/восстановление диалога."""

    user_id: int = Field(gt=0)


class DialogFinalizeRequest(BaseModel):
    """Запрос на финализацию диалога (критерии валидирует сценарий)."""

    user_id: int = Field(gt=0)
    criteria: dict[str, Any] | None = None


class DialogReplyResponse(BaseModel):
    """Ответ агента на один ход."""

    session_id: str
    text: str
    options: list[str]
    status: str
    finalize_ready: bool
    turn_count: int
    criteria: dict[str, Any]


class DialogRestoreResponse(BaseModel):
    """Результат восстановления диалога из архива."""

    restored: bool
    reason: str | None = None
    session_id: str | None = None


# ---------------------------------------------------------------------------
# Маппинг ошибок прикладного слоя в HTTP
# ---------------------------------------------------------------------------


def _raise_http(exc: AppError) -> NoReturn:
    """Прикладная ошибка → HTTP-ответ (единственное место маппинга)."""
    if isinstance(exc, NotFound):
        raise HTTPException(status.HTTP_404_NOT_FOUND, exc.public_message)
    if isinstance(exc, Forbidden):
        raise HTTPException(status.HTTP_403_FORBIDDEN, exc.public_message)
    if isinstance(exc, InvalidInput):
        raise HTTPException(422, exc.public_message)
    if isinstance(exc, AiUnavailable):
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "AI temporarily unavailable"
        )
    logger.error("dialog_unexpected_app_error code=%s", exc.code)
    raise HTTPException(status.HTTP_400_BAD_REQUEST, exc.public_message)


def _to_response(reply: DialogReply) -> DialogReplyResponse:
    """DTO ответа → публичная модель."""
    return DialogReplyResponse(
        session_id=reply.session_id,
        text=reply.text,
        options=list(reply.options),
        status=reply.status,
        finalize_ready=reply.finalize_ready,
        turn_count=reply.turn_count,
        criteria=reply.criteria,
    )


# ---------------------------------------------------------------------------
# Эндпоинты
# ---------------------------------------------------------------------------


@router.post("/message", response_model=DialogReplyResponse)
async def dialog_message(
    body: DialogMessageRequest,
    use_case: SendDialogMessageDep,
) -> DialogReplyResponse:
    """Один ход AI-агента.

    Raises:
        HTTPException: 404 если пользователя нет; 403 если деактивирован;
            503 если LLM недоступна.
    """
    try:
        reply = await use_case.execute(
            DialogMessageCommand(user_id=body.user_id, text=body.text)
        )
    except AppError as exc:
        _raise_http(exc)
    return _to_response(reply)


@router.post("/restore", response_model=DialogRestoreResponse)
async def dialog_restore(
    body: DialogResetRequest,
    use_case: RestoreDialogDep,
) -> DialogRestoreResponse:
    """Поднять последний диалог из архива (п. «посмотреть диалог спустя время»)."""
    try:
        result: DialogRestoreResult | None = await use_case.execute(body.user_id)
    except Exception:  # noqa: BLE001 — контракт: 500 «Restore failed»
        logger.exception("dialog_restore_failed user_id=%s", body.user_id)
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR, "Restore failed"
        )
    if result is None:
        return DialogRestoreResponse(restored=False, reason="not_found")
    return DialogRestoreResponse(
        restored=result.restored,
        reason=result.reason,
        session_id=result.session_id,
    )


@router.post("/reset")
async def dialog_reset(
    body: DialogResetRequest,
    use_case: ResetDialogDep,
) -> dict[str, bool]:
    """Сбросить текущий диалог пользователя."""
    await use_case.execute(body.user_id)
    return {"reset": True}


@router.post("/finalize")
async def dialog_finalize(
    body: DialogFinalizeRequest,
    use_case: FinalizeDialogDep,
) -> dict[str, Any]:
    """Финализация: сохранить фильтр + поставить задачи парсинга."""
    try:
        result = await use_case.execute(
            DialogFinalizeCommand(user_id=body.user_id, criteria=body.criteria)
        )
    except AppError as exc:
        _raise_http(exc)
    return {"criteria": result.criteria, "sources": list(result.sources)}
