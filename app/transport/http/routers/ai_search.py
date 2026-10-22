"""AI-поиск для Mini App и сайта (JWT) — тонкий контроллер.

Пользователь берётся только из токена (`CurrentUserDep`), id из тела не
принимается — иначе IDOR. Сообщения защищены per-user rate-limit'ом
(сценарий `SendAiMessage`), квота считается в `application/quota`.

`GET /dialog/{session_id}` отдаёт архивный диалог **только владельцу**:
чужая или несуществующая сессия — 404 (существование не раскрывается).
"""


from __future__ import annotations
from app.transport.http.deps.dishka import DishkaRoute

import logging
from datetime import datetime
from typing import Any, NoReturn

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.application.ai_search.use_cases import RateLimited
from app.application.dto.ai_search import AiMessageCommand
from app.application.dto.dialog import DialogFinalizeCommand
from app.application.errors import AppError, Forbidden, InvalidInput, NotFound
from app.application.dialog.use_cases import AiUnavailable
from app.transport.http.deps.auth import CurrentUserDep
from app.transport.http.deps.use_cases import (
    FinalizeDialogDep,
    GetDialogDep,
    ResetDialogDep,
    SendAiMessageDep,
)
from app.transport.http.routers.dialog import DialogReplyResponse, _to_response

logger = logging.getLogger(__name__)

router = APIRouter(
        route_class=DishkaRoute,
        prefix="/api/ai", tags=["ai-search"])

_TEXT_MAX_LEN = 4000


# ---------------------------------------------------------------------------
# Схемы
# ---------------------------------------------------------------------------


class AiMessageRequest(BaseModel):
    """Запрос на один ход AI-агента."""

    text: str = Field(min_length=1, max_length=_TEXT_MAX_LEN)


class AiFinalizeRequest(BaseModel):
    """Запрос на финализацию диалога (критерии валидирует сценарий)."""

    criteria: dict[str, Any] | None = None


class DialogHistoryOut(BaseModel):
    """Архивный диалог пользователя."""

    session_id: str
    status: str
    criteria: dict[str, Any]
    turn_count: int
    messages: list[dict[str, Any]]
    created_at: datetime | None = None
    updated_at: datetime | None = None


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
    if isinstance(exc, RateLimited):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, exc.public_message)
    if isinstance(exc, AiUnavailable):
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "AI temporarily unavailable"
        )
    logger.error("ai_unexpected_app_error code=%s", exc.code)
    raise HTTPException(status.HTTP_400_BAD_REQUEST, exc.public_message)


# ---------------------------------------------------------------------------
# Эндпоинты
# ---------------------------------------------------------------------------


@router.post("/message", response_model=DialogReplyResponse)
async def ai_message(
    body: AiMessageRequest,
    user: CurrentUserDep,
    use_case: SendAiMessageDep,
) -> DialogReplyResponse:
    """Один ход AI-агента: текст → ответ + опции + критерии.

    Raises:
        HTTPException: 429 при превышении rate-limit; 503 при недоступности
            LLM; 404/403 если пользователь пропал/забанен между запросами.
    """
    try:
        reply = await use_case.execute(
            AiMessageCommand(user_id=user.telegram_id, text=body.text)
        )
    except AppError as exc:
        _raise_http(exc)
    return _to_response(reply)


@router.post("/reset")
async def ai_reset(
    user: CurrentUserDep,
    use_case: ResetDialogDep,
) -> dict[str, bool]:
    """Сбросить состояние диалога пользователя."""
    await use_case.execute(user.telegram_id)
    return {"reset": True}


@router.post("/finalize")
async def ai_finalize(
    body: AiFinalizeRequest,
    user: CurrentUserDep,
    use_case: FinalizeDialogDep,
) -> dict[str, Any]:
    """Финализация диалога: сохранить фильтр + поставить задачи парсинга."""
    try:
        result = await use_case.execute(
            DialogFinalizeCommand(
                user_id=user.telegram_id, criteria=body.criteria
            )
        )
    except AppError as exc:
        _raise_http(exc)
    logger.info(
        "ai_finalize user_id=%s criteria_keys=%s",
        user.telegram_id,
        sorted(result.criteria) if result.criteria else None,
    )
    return {"criteria": result.criteria, "sources": list(result.sources)}


@router.get("/dialog/{session_id}", response_model=DialogHistoryOut)
async def ai_dialog_history(
    session_id: str,
    user: CurrentUserDep,
    use_case: GetDialogDep,
) -> DialogHistoryOut:
    """Архивный диалог по id — только свой (чужой → 404, IDOR)."""
    try:
        view = await use_case.execute(user.telegram_id, session_id)
    except AppError as exc:
        _raise_http(exc)
    return DialogHistoryOut(
        session_id=view.session_id,
        status=view.status,
        criteria=view.criteria,
        turn_count=view.turn_count,
        messages=list(view.messages),
        created_at=view.created_at,
        updated_at=view.updated_at,
    )
