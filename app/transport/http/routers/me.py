"""Профиль пользователя: GET /api/me и POST /api/me/onboarded (ТЗ п. 4.3).

Тонкий контроллер: оба эндпоинта вызывают сценарии из
`app/application/profile/use_cases.py` и отдают `UserOut`.

`UserOut` **не должен** содержать `password_hash`/`email` и не должен
иметь relationship-полей без eager-load (иначе `MissingGreenlet`).
Секреты не доходят до схемы ещё и потому, что сценарий возвращает DTO
`UserProfile` — в нём просто нет этих полей.
См. `tests/schemas/test_user.py::test_no_sensitive_fields`.
"""

from app.transport.http.deps.dishka import DishkaRoute

import logging
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict

from app.application.errors import AppError
from app.transport.http.deps.auth import CurrentUserDep
from app.transport.http.deps.use_cases import (
    CompleteOnboardingDep,
    GetProfileDep,
    ListMyAnalysesDep,
)
from app.transport.http.schemas.user import UserOut

logger = logging.getLogger(__name__)

router = APIRouter(route_class=DishkaRoute, prefix="/api", tags=["users"])

_STATUS_BY_CODE = {
    "not_found": status.HTTP_404_NOT_FOUND,
    "forbidden": status.HTTP_403_FORBIDDEN,
}


def _http_error(exc: AppError) -> HTTPException:
    """Перевод прикладной ошибки в HTTP-ответ."""
    return HTTPException(
        _STATUS_BY_CODE.get(exc.code, status.HTTP_400_BAD_REQUEST),
        exc.public_message,
    )


class MyAnalysisOut(BaseModel):
    """Строка истории «Мои анализы»: разбор + карточка машины."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    listing_id: int
    verdict: str
    fair_min: float | None = None
    fair_max: float | None = None
    summary: str | None = None
    created_at: datetime
    make: str | None = None
    model: str | None = None
    year: int | None = None
    price: float | None = None
    currency: str | None = None
    image: str | None = None  # первое фото — превью в списке


@router.get("/me", response_model=UserOut)
async def me(user: CurrentUserDep, use_case: GetProfileDep) -> UserOut:
    """Профиль текущего пользователя из JWT.

    Args:
        user: Текущий пользователь (dishka: `CurrentUserDep`).
        use_case: Сценарий чтения профиля.

    Returns:
        `UserOut` с публичными полями профиля.

    Raises:
        HTTPException: 404 если пользователя уже нет.
    """
    try:
        profile = await use_case.execute(user.telegram_id)
    except AppError as exc:
        raise _http_error(exc) from exc
    return UserOut.model_validate(profile)


@router.post("/me/onboarded", response_model=UserOut)
async def me_onboarded(
    user: CurrentUserDep, use_case: CompleteOnboardingDep
) -> UserOut:
    """Пометить онбординг пройденным (UI/UX ТЗ §2.8). Идемпотентно.

    Первый вызов сохраняет `onboarded_at`, повторные — no-op: онбординг
    отражает **первый** факт прохождения (для когорт/retention).

    Args:
        user: Текущий пользователь (dishka: `CurrentUserDep`).
        use_case: Сценарий онбординга.

    Returns:
        `UserOut` (с актуальным `onboarded_at`).
    """
    try:
        result = await use_case.execute(user.telegram_id)
    except AppError as exc:
        raise _http_error(exc) from exc
    return UserOut.model_validate(result.profile)


@router.get("/me/analyses", response_model=list[MyAnalysisOut])
async def my_analyses(
    user: CurrentUserDep,
    use_case: ListMyAnalysesDep,
    limit: int = Query(50, ge=1, le=100),
) -> list[MyAnalysisOut]:
    """История AI-разборов пользователя (вкладка «Мои анализы»).

    Владелец берётся из токена; чужие разборы сюда не попадают.

    Args:
        user: Текущий пользователь (dishka: `CurrentUserDep`).
        use_case: Сценарий чтения истории.
        limit: Сколько последних разборов отдать (1..100).

    Returns:
        Разборы, новые сверху.

    Raises:
        HTTPException: Прикладная ошибка сценария (маппинг `_http_error`).
    """
    try:
        items = await use_case.execute(user.telegram_id, limit=limit)
    except AppError as exc:
        raise _http_error(exc) from exc
    return [MyAnalysisOut.model_validate(item) for item in items]
