"""AI-поиск для Mini App и сайта (JWT-авторизация, этап «ИИ-поиск в аппке»).

Тот же агент, что у бота, но пользователь берётся из JWT (CurrentUserDep),
а не из тела запроса с internal-токеном. Финализация ставит fetch-задачи
в те же очереди RabbitMQ.
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.api.dialog import DialogReplyResponse, _enqueue_fetch, _to_response
from app.ai.agent import run_agent
from app.ai.llm_client import build_llm_client
from app.core.config import get_settings
from app.core.deps import CurrentUserDep, SessionDep
from app.core.deps_internal import get_redis
from app.services.dialog import DialogStore, finalize_dialog, reset_dialog
from redis.asyncio import Redis

router = APIRouter(prefix="/api/ai", tags=["ai-search"])


class AiMessageRequest(BaseModel):
    text: str


class AiFinalizeRequest(BaseModel):
    criteria: dict[str, Any] | None = None


@router.post("/message", response_model=DialogReplyResponse)
async def ai_message(
    body: AiMessageRequest,
    user: CurrentUserDep,
    session: SessionDep,
    redis: Redis = Depends(get_redis),
) -> DialogReplyResponse:
    """Один ход AI-агента: текст → ответ + опции + критерии."""
    try:
        llm = build_llm_client(get_settings())
    except Exception as exc:  # нет ключа/конфига LLM
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, f"LLM unavailable: {exc}"
        ) from exc
    try:
        reply = await run_agent(
            user.id,
            body.text,
            session=session,
            redis=redis,
            llm=llm,
            finalize=lambda c: _enqueue_fetch(session, c),
        )
    finally:
        await llm.aclose()
    return _to_response(reply)


@router.post("/reset")
async def ai_reset(
    user: CurrentUserDep,
    session: SessionDep,
    redis: Redis = Depends(get_redis),
) -> dict[str, bool]:
    await reset_dialog(user.id, session=session, store=DialogStore(redis))
    return {"reset": True}


@router.post("/finalize")
async def ai_finalize(
    body: AiFinalizeRequest,
    user: CurrentUserDep,
    session: SessionDep,
    redis: Redis = Depends(get_redis),
) -> dict[str, Any]:
    """Финализация диалога: сохранить фильтр + поставить задачи парсинга."""
    return await finalize_dialog(
        user.id,
        session=session,
        store=DialogStore(redis),
        enqueue=lambda c: _enqueue_fetch(session, c),
        criteria=body.criteria,
    )
