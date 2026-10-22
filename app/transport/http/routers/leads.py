"""Лиды (обращения к менеджеру): создание ботом/Mini App, смена статуса менеджером.

Оба эндпоинта внутренние (X-Internal-Token). Тонкий контроллер: разбор
входа, вызов сценария, маппинг ошибок. Правила (FK-валидация,
идемпотентность, частичный PATCH, событие воронки `lead_created`) живут в
`app/application/leads/use_cases.py`, SQL — в
`app/infrastructure/db/repositories/leads.py`.

`POST /api/leads` идемпотентен по `Idempotency-Key`: повтор возвращает тот
же лид (201 с тем же телом), а не дубль.
"""

from app.transport.http.deps.dishka import InternalDishkaRoute

import logging

from fastapi import APIRouter, Header, HTTPException, Path, status

from app.application.dto.leads import LeadPatch
from app.application.errors import AppError
from app.application.leads.use_cases import CreateLeadCommand
from app.transport.http.deps.use_cases import CreateLeadDep, UpdateLeadDep
from app.transport.http.schemas.lead import LeadCreateIn, LeadOut, LeadStatusIn

logger = logging.getLogger(__name__)

router = APIRouter(route_class=InternalDishkaRoute,
    prefix="/api/leads",
    tags=["leads"],
)

_STATUS_BY_CODE = {
    "not_found": status.HTTP_404_NOT_FOUND,
    "conflict": status.HTTP_409_CONFLICT,
    "invalid_input": status.HTTP_422_UNPROCESSABLE_ENTITY,
}


def _http_error(exc: AppError) -> HTTPException:
    """Перевод прикладной ошибки в HTTP-ответ."""
    return HTTPException(
        _STATUS_BY_CODE.get(exc.code, status.HTTP_400_BAD_REQUEST),
        exc.public_message,
    )


@router.post("", response_model=LeadOut, status_code=status.HTTP_201_CREATED)
async def create_lead(
    body: LeadCreateIn,
    use_case: CreateLeadDep,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> LeadOut:
    """Создать лид.

    Если передан `Idempotency-Key`, повторный запрос с тем же ключом
    вернёт существующий лид, а не создаст дубль.

    Args:
        body: Данные лида.
        use_case: Сценарий создания лида.
        idempotency_key: Опциональный ключ идемпотентности.

    Returns:
        `LeadOut`.

    Raises:
        HTTPException: 404 если user/listing не найдены; 409 если вставка
            конфликтует с существующей строкой.
    """
    try:
        lead = await use_case.execute(
            CreateLeadCommand(
                user_id=body.user_id,
                listing_id=body.listing_id,
                source=body.source,
                comment=body.comment,
                idempotency_key=idempotency_key,
            )
        )
    except AppError as exc:
        raise _http_error(exc) from exc
    return LeadOut.model_validate(lead)


@router.patch("/{lead_id}", response_model=LeadOut)
async def update_lead(
    body: LeadStatusIn,
    use_case: UpdateLeadDep,
    lead_id: int = Path(ge=1),
) -> LeadOut:
    """Смена статуса/комментария/менеджера лида (частично).

    Непереданные поля не меняются; пустой патч отклоняет схема (422).

    Args:
        body: Патч (поля опциональны).
        use_case: Сценарий обновления лида.
        lead_id: ID лида.

    Returns:
        `LeadOut`.

    Raises:
        HTTPException: 404 если лид не найден; 422 если manager_id не существует.
    """
    try:
        lead = await use_case.execute(
            lead_id,
            LeadPatch(
                status=body.status,
                manager_id=body.manager_id,
                comment=body.comment,
            ),
        )
    except AppError as exc:
        raise _http_error(exc) from exc
    return LeadOut.model_validate(lead)
