"""Эндпоинты уведомлений (этап 4, ТЗ п. 8.6): настройки /stop //resume
и ручной запуск скана фильтров."""

from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select

from app.queue import enqueue_task
from app.core.deps import SessionDep
from app.core.deps_internal import check_internal_token
from app.db.models import UserFilter

router = APIRouter(
    prefix="/api/notifications",
    tags=["notifications"],
    dependencies=[Depends(check_internal_token)],
)


class NotifySettingsIn(BaseModel):
    user_id: int
    notify_mode: Literal["instant", "digest", "off"]
    quiet_hours: str | None = None  # "23:00-08:00" или None


@router.post("/settings")
async def update_settings(payload: NotifySettingsIn, session: SessionDep) -> dict:
    """Режим уведомлений применяется ко всем фильтрам пользователя."""
    result = await session.execute(
        select(UserFilter).where(UserFilter.user_id == payload.user_id)
    )
    filters = list(result.scalars().all())
    now = datetime.now(timezone.utc)
    for flt in filters:
        flt.notify_mode = payload.notify_mode
        if payload.quiet_hours is not None:
            flt.quiet_hours = payload.quiet_hours
        flt.updated_at = now
    await session.commit()
    return {"user_id": payload.user_id, "updated": len(filters)}


@router.post("/scan")
async def trigger_scan(session: SessionDep) -> dict:
    """Ручной запуск скана фильтров (обычно его делает Fetch Worker)."""
    await enqueue_task("notify_new_listings")
    return {"enqueued": True}