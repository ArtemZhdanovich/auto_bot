"""Эндпоинты уведомлений (этап 4, ТЗ п. 8.6): настройки /stop //resume
и ручной запуск скана фильтров.

Тонкий контроллер: разбор тела, вызов сценария и маппинг ошибок в HTTP.
Ни SQL, ни транзакций, ни импортов инфраструктуры — настройки и скан
живут в `app/application/notifications/settings.py`, очередь за портом
`Enqueuer`, dedup за портом `IdempotencyGuard`.

Формат `quiet_hours` проверяется схемой (422 до вызова сценария);
семантика (взаимоисключение с `quiet_hours_clear`, tier-bypass) — в сценарии.
"""

from app.transport.http.deps.dishka import InternalDishkaRoute

import logging
import re
from typing import Literal

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field, field_validator

from app.application.errors import AppError
from app.application.catalog.dto import NotifySettingsCommand
from app.transport.http.deps.use_cases import (
    TriggerScanDep,
    UpdateNotifySettingsDep,
)

logger = logging.getLogger(__name__)

router = APIRouter(route_class=InternalDishkaRoute,
    prefix="/api/notifications",
    tags=["notifications"],
)

_QUIET_HOURS_RE = r"^([01]\d|2[0-3]):[0-5]\d-([01]\d|2[0-3]):[0-5]\d$"

_STATUS_BY_CODE = {
    "not_found": status.HTTP_404_NOT_FOUND,
    "forbidden": status.HTTP_403_FORBIDDEN,
    "conflict": status.HTTP_409_CONFLICT,
    "invalid_input": status.HTTP_422_UNPROCESSABLE_ENTITY,
    "queue_unavailable": status.HTTP_503_SERVICE_UNAVAILABLE,
}


def _http_error(exc: AppError) -> HTTPException:
    """Перевод прикладной ошибки в HTTP-ответ."""
    return HTTPException(
        _STATUS_BY_CODE.get(exc.code, status.HTTP_400_BAD_REQUEST),
        exc.public_message,
    )


class NotifySettingsIn(BaseModel):
    """Настройки уведомлений, применяемые ко всем фильтрам пользователя."""

    user_id: int = Field(gt=0)
    # None = не менять режим (только тихие часы)
    notify_mode: Literal["instant", "digest", "off"] | None = None
    quiet_hours: str | None = Field(default=None, max_length=11)  # "23:00-08:00"
    # Явно сбросить тихие часы (отличить от «не передано»)
    quiet_hours_clear: bool = False

    @field_validator("quiet_hours")
    @classmethod
    def _check_quiet_hours(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if not re.match(_QUIET_HOURS_RE, v):
            raise ValueError(
                "quiet_hours must be HH:MM-HH:MM (e.g. '23:00-08:00')"
            )
        start, end = v.split("-")
        if start == end:
            raise ValueError("quiet_hours start and end must differ")
        return v


class UpdateSettingsOut(BaseModel):
    user_id: int
    updated: int
    detail: str | None = None


class ScanTriggerOut(BaseModel):
    enqueued: bool
    job_id: str | None = None


@router.post("/settings", response_model=UpdateSettingsOut)
async def update_settings(
    payload: NotifySettingsIn, use_case: UpdateNotifySettingsDep
) -> UpdateSettingsOut:
    """Режим уведомлений и тихие часы применяются ко всем фильтрам пользователя.

    Args:
        payload: Настройки (поля опциональны; `quiet_hours_clear` сбрасывает).
        use_case: Сценарий обновления настроек.

    Returns:
        `UpdateSettingsOut` с количеством обновлённых фильтров.

    Raises:
        HTTPException: 404 если пользователь не найден; 403 если деактивирован
            или `instant` без Pro; 422 при конфликте `quiet_hours` и
            `quiet_hours_clear`.
    """
    try:
        result = await use_case.execute(
            NotifySettingsCommand(
                user_id=payload.user_id,
                notify_mode=payload.notify_mode,
                quiet_hours=payload.quiet_hours,
                quiet_hours_clear=payload.quiet_hours_clear,
            )
        )
    except AppError as exc:
        raise _http_error(exc) from exc

    return UpdateSettingsOut(
        user_id=result.user_id, updated=result.updated, detail=result.detail
    )


@router.post("/scan", response_model=ScanTriggerOut)
async def trigger_scan(use_case: TriggerScanDep) -> ScanTriggerOut:
    """Ручной запуск скана фильтров (обычно его делает Fetch Worker).

    Идемпотентность: не более 1 скана в минуту — повторный POST → 409
    (защита от двойного клика и спама internal-token'ом).

    Args:
        use_case: Сценарий запуска скана.

    Returns:
        `ScanTriggerOut` с `job_id`.

    Raises:
        HTTPException: 409 если скан уже запущен в последнюю минуту;
            503 если очередь недоступна.
    """
    try:
        result = await use_case.execute()
    except AppError as exc:
        raise _http_error(exc) from exc

    return ScanTriggerOut(enqueued=True, job_id=result.job_id)
