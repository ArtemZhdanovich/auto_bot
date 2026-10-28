"""Сделки: агент на договор/осмотр (Business) и «свяжитесь со мной» (все)."""

from __future__ import annotations

from app.transport.http.deps.dishka import DishkaRoute

import logging
from uuid import UUID

from fastapi import APIRouter, HTTPException, Path, status
from pydantic import BaseModel, Field

from app.application.errors import AppError
from app.transport.http.deps.auth import CurrentUserDep
from app.transport.http.deps.use_cases import RequestAgentDep, RequestContactDep

logger = logging.getLogger(__name__)

router = APIRouter(route_class=DishkaRoute, prefix="/api/listings", tags=["deals"])


class AgentRequestIn(BaseModel):
    """Запрос агента: опциональный комментарий к сделке."""

    comment: str | None = Field(default=None, max_length=500)


class AgentRequestOut(BaseModel):
    """Подтверждение заявки: лид + deal-тред для переписки (§5)."""

    lead_id: int
    status: str = "requested"
    thread_id: UUID | None = None


class ContactRequestIn(BaseModel):
    """Заявка «свяжитесь со мной»: опциональный комментарий (макс 500)."""

    comment: str | None = Field(default=None, max_length=500)


class ContactRequestOut(BaseModel):
    """Подтверждение заявки на контакт."""

    lead_id: int
    status: str = "requested"


_STATUS_BY_CODE = {
    "not_found": status.HTTP_404_NOT_FOUND,
    "forbidden": status.HTTP_403_FORBIDDEN,
    "conflict": status.HTTP_409_CONFLICT,
}


@router.post("/{listing_id}/agent", response_model=AgentRequestOut, status_code=201)
async def request_agent(
    user: CurrentUserDep,
    body: AgentRequestIn,
    use_case: RequestAgentDep,
    listing_id: int = Path(ge=1),
) -> AgentRequestOut:
    """Отправить агента на сделку (только тариф Business).

    Создаёт лид `source=agent` и deal-тред: агент подключится прямо в
    этот чат после claim. Повторная заявка по той же машине идемпотентна
    (тот же лид и тот же тред).
    """
    try:
        result = await use_case.execute(
            user.telegram_id, listing_id, body.comment
        )
    except AppError as exc:
        raise HTTPException(
            _STATUS_BY_CODE.get(exc.code, status.HTTP_400_BAD_REQUEST),
            exc.public_message,
        ) from exc
    return AgentRequestOut(
        lead_id=result.lead.id,
        status=result.lead.status or "requested",
        thread_id=result.thread_id,
    )


@router.post("/{listing_id}/contact", response_model=ContactRequestOut, status_code=201)
async def request_contact(
    user: CurrentUserDep,
    body: ContactRequestIn,
    use_case: RequestContactDep,
    listing_id: int = Path(ge=1),
) -> ContactRequestOut:
    """Заявка «Связаться с партнёром/менеджером» (любой тариф).

    Создаёт лид `source=web` — уходит в пул партнёров (CPL). Гостям
    недоступна (после входа); повторная заявка по той же машине в
    течение суток — 409 `duplicate_contact`.
    """
    try:
        lead = await use_case.execute(user.telegram_id, listing_id, body.comment)
    except AppError as exc:
        raise HTTPException(
            _STATUS_BY_CODE.get(exc.code, status.HTTP_400_BAD_REQUEST),
            exc.public_message,
        ) from exc
    return ContactRequestOut(lead_id=lead.id, status=lead.status or "requested")
