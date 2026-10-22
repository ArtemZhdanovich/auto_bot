"""CRUD фильтров пользователя для Mini App (этап 5, ТЗ п. 9.2.3).

Тонкий транспорт: разбор параметров, вызов сценария и маппинг в схему.
Лимит тарифа, downgrade-bypass `instant` и проверка владельца (IDOR) —
в `app.application.filters.use_cases`; SQL — в репозитории фильтров.
"""


from __future__ import annotations
from app.transport.http.deps.dishka import DishkaRoute

from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query, status
from pydantic import BaseModel

from app.application.dto.filters import FilterDraft, FilterPatch
from app.application.errors import (
    AppError,
    Conflict,
    Forbidden,
    InvalidInput,
    NotFound,
)
from app.application.filters.use_cases import (
    CreateFilterCommand,
    DeleteFilterCommand,
    ListFiltersQuery,
    UpdateFilterCommand,
)
from app.transport.http.deps.auth import CurrentUserDep
from app.transport.http.deps.use_cases import (
    CreateFilterDep,
    DeleteFilterDep,
    ListFiltersDep,
    UpdateFilterDep,
)
from app.transport.http.schemas.filter import FilterCreate, FilterOut, FilterUpdate

router = APIRouter(route_class=DishkaRoute, prefix="/api/filters", tags=["filters"])

_LIST_LIMIT_DEFAULT = 50
_LIST_LIMIT_MAX = 200

_STATUS_BY_ERROR = (
    (NotFound, status.HTTP_404_NOT_FOUND),
    (Forbidden, status.HTTP_403_FORBIDDEN),
    (Conflict, status.HTTP_409_CONFLICT),
    (InvalidInput, 422),
)


class FilterDeletedOut(BaseModel):
    """Ответ на удаление фильтра (для документации; 204 без body)."""

    deleted: bool = True


def _to_http(exc: AppError) -> HTTPException:
    """Перевести прикладную ошибку в HTTP-статус с публичным текстом."""
    for error_type, code in _STATUS_BY_ERROR:
        if isinstance(exc, error_type):
            return HTTPException(status_code=code, detail=exc.public_message)
    return HTTPException(status_code=400, detail=exc.public_message)


@router.get("", response_model=list[FilterOut])
async def list_filters(
    user: CurrentUserDep,
    use_case: ListFiltersDep,
    limit: int = Query(default=_LIST_LIMIT_DEFAULT, ge=1, le=_LIST_LIMIT_MAX),
    offset: int = Query(default=0, ge=0),
) -> list[FilterOut]:
    """Список фильтров пользователя (по `created_at`), с пагинацией."""
    records = await use_case.execute(
        ListFiltersQuery(
            user_id=user.telegram_id, limit=limit, offset=offset
        )
    )
    return [FilterOut.model_validate(record) for record in records]


@router.post("", response_model=FilterOut, status_code=status.HTTP_201_CREATED)
async def create_filter(
    body: FilterCreate, user: CurrentUserDep, use_case: CreateFilterDep
) -> FilterOut:
    """Создание фильтра: лимит по тарифу (3/20/∞) + instant только в Pro."""
    try:
        record = await use_case.execute(
            CreateFilterCommand(
                user=user,
                draft=FilterDraft(
                    name=body.name,
                    criteria=body.criteria.model_dump(
                        mode="json", exclude_none=True
                    ),
                    notify_mode=body.notify_mode,
                    quiet_hours=body.quiet_hours,
                ),
            )
        )
    except AppError as exc:
        raise _to_http(exc) from exc
    return FilterOut.model_validate(record)


@router.patch("/{filter_id}", response_model=FilterOut)
async def update_filter(
    body: FilterUpdate,
    user: CurrentUserDep,
    use_case: UpdateFilterDep,
    filter_id: int = Path(ge=1),
) -> FilterOut:
    """Обновление фильтра (частичное).

    Проверяется **итоговое** значение `notify_mode`: пользователь,
    потерявший Pro, не сохранит `instant`.
    """
    try:
        record = await use_case.execute(
            UpdateFilterCommand(
                user=user,
                filter_id=filter_id,
                patch=FilterPatch(
                    name=body.name,
                    criteria=(
                        body.criteria.model_dump(mode="json", exclude_none=True)
                        if body.criteria is not None
                        else None
                    ),
                    notify_mode=body.notify_mode,
                    quiet_hours=body.quiet_hours,
                ),
            )
        )
    except AppError as exc:
        raise _to_http(exc) from exc
    return FilterOut.model_validate(record)


@router.delete("/{filter_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_filter(
    user: CurrentUserDep,
    use_case: DeleteFilterDep,
    filter_id: Annotated[int, Path(ge=1)],
) -> None:
    """Удаление фильтра (чужой → 404)."""
    try:
        await use_case.execute(
            DeleteFilterCommand(user=user, filter_id=filter_id)
        )
    except AppError as exc:
        raise _to_http(exc) from exc
