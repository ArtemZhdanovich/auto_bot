"""Диалоговый API (этап 3, ТЗ п. 7). Бот ходит сюда с internal-токеном."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.agent import AgentReply, run_agent
from app.ai.llm_client import build_llm_client
from app.core.config import get_settings
from app.core.deps_internal import check_internal_token, get_redis
from app.db.models import Source
from app.db.session import get_session
from app.queue import enqueue_task
from app.schemas.criteria import SearchCriteria
from app.db.models import User
from app.services.analytics import EVENT_DIALOG_MESSAGE, track
from app.services.subscription import tier_config
from app.services.dialog import (
    DialogStore,
    archive_session,
    finalize_dialog,
    reset_dialog,
    restore_dialog,
)
from redis.asyncio import Redis

router = APIRouter(
    prefix="/api/dialog", tags=["dialog"], dependencies=[Depends(check_internal_token)]
)


class DialogMessageRequest(BaseModel):
    user_id: int
    text: str


class DialogResetRequest(BaseModel):
    user_id: int


class DialogFinalizeRequest(BaseModel):
    user_id: int
    criteria: dict[str, Any] | None = None


class DialogReplyResponse(BaseModel):
    session_id: str
    text: str
    options: list[str]
    status: str
    finalize_ready: bool
    turn_count: int
    criteria: dict[str, Any]


def _to_response(reply: AgentReply) -> DialogReplyResponse:
    return DialogReplyResponse(
        session_id=reply.session_id,
        text=reply.text,
        options=reply.options,
        status=reply.status,
        finalize_ready=reply.finalize_ready,
        turn_count=reply.turn_count,
        criteria=reply.criteria,
    )


async def _enqueue_fetch(session: AsyncSession, criteria: SearchCriteria) -> list[str]:
    """Поставить задачи парсинга по всем включённым источникам (RabbitMQ)."""
    names = list(
        (
            await session.execute(select(Source.name).where(Source.enabled.is_(True)))
        )
        .scalars()
        .all()
    )
    if not names:
        return []
    for name in names:
        await enqueue_task(
            "fetch_source_task",
            name,
            criteria.model_dump(mode="json"),
            1,  # интерактивный поиск: 1 страница, юзер ждёт в диалоге
        )
    return names


@router.post("/message", response_model=DialogReplyResponse)
async def dialog_message(
    body: DialogMessageRequest,
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis),
) -> DialogReplyResponse:
    try:
        llm = build_llm_client(get_settings())
    except Exception as exc:  # нет ключа/конфига LLM
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, f"LLM unavailable: {exc}"
        ) from exc
    try:
        # Квота тарифа: списывается ЗА ВЫДАЧУ РЕЗУЛЬТАТА внутри агента
        # (preview с вариантами / finalize), не за сообщение — иначе
        # демо-юзер не может пообщаться (фидбек «1 сообщение ≠ 1 поиск»).
        user = await session.get(User, body.user_id)
        quota_limit = tier_config(user).ai_daily_limit if user else 3
        reply = await run_agent(
            body.user_id,
            body.text,
            session=session,
            redis=redis,
            llm=llm,
            finalize=lambda c: _enqueue_fetch(session, c),
            quota_limit=quota_limit,
        )
    finally:
        await llm.aclose()
    # Архив сессии на каждом ходу (п. «сессии сохраняются»): диалог можно
    # посмотреть/продолжить позже, даже если финализации не было.
    state = await DialogStore(redis).load(body.user_id)
    if state is not None:
        await archive_session(session, state)
        await session.commit()
    await track(session, body.user_id, EVENT_DIALOG_MESSAGE)
    return _to_response(reply)


@router.post("/restore")
async def dialog_restore(
    body: DialogResetRequest,
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis),
) -> dict[str, Any]:
    """Поднять последний диалог из архива (п. «посмотреть диалог спустя время»)."""
    result = await restore_dialog(
        body.user_id, session=session, store=DialogStore(redis)
    )
    return result or {"restored": False, "reason": "not_found"}


@router.post("/reset")
async def dialog_reset(
    body: DialogResetRequest,
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis),
) -> dict[str, bool]:
    await reset_dialog(body.user_id, session=session, store=DialogStore(redis))
    return {"reset": True}


@router.post("/finalize")
async def dialog_finalize(
    body: DialogFinalizeRequest,
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis),
) -> dict[str, Any]:
    return await finalize_dialog(
        body.user_id,
        session=session,
        store=DialogStore(redis),
        enqueue=lambda c: _enqueue_fetch(session, c),
        criteria=body.criteria,
    )
