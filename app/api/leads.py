"""Лиды (обращения к менеджеру): создание ботом/Mini App, смена статуса менеджером.

Оба эндпоинта внутренние (X-Internal-Token). Создание лида пишет событие
воронки `lead_created` (P1-7) — замыкает конверсию диалог → лид.
"""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status

from app.core.deps import SessionDep
from app.core.deps_internal import check_internal_token
from app.db.models import Lead
from app.schemas.lead import LeadCreateIn, LeadOut, LeadStatusIn
from app.services.analytics import EVENT_LEAD_CREATED, track

router = APIRouter(
    prefix="/api/leads",
    tags=["leads"],
    dependencies=[Depends(check_internal_token)],
)


@router.post("", response_model=LeadOut, status_code=status.HTTP_201_CREATED)
async def create_lead(body: LeadCreateIn, session: SessionDep) -> LeadOut:
    lead = Lead(
        user_id=body.user_id,
        listing_id=body.listing_id,
        source=body.source,
        status="new",  # явный дефолт: колоночный default= срабатывает только при flush
        comment=body.comment,
    )
    session.add(lead)
    await session.commit()
    await session.refresh(lead)
    await track(
        session,
        body.user_id,
        EVENT_LEAD_CREATED,
        {"listing_id": body.listing_id, "source": body.source},
    )
    return LeadOut.model_validate(lead)


@router.patch("/{lead_id}", response_model=LeadOut)
async def update_lead(
    lead_id: int, body: LeadStatusIn, session: SessionDep
) -> LeadOut:
    lead = await session.get(Lead, lead_id)
    if lead is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Lead not found")
    lead.status = body.status
    if body.manager_id is not None:
        lead.manager_id = body.manager_id
    if body.comment is not None:
        lead.comment = body.comment
    lead.updated_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(lead)
    return LeadOut.model_validate(lead)
