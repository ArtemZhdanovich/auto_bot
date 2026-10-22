"""Внутренние эндпоинты для бота (бот не ходит в БД напрямую — ТЗ п. 4.1).

`InternalDishkaRoute` — ни один эндпоинт этого роутера не может
«забыть» проверку `X-Internal-Token`: она выполняется в route-классе
до выполнения эндпоинта, OpenAPI показывает header `X-Internal-Token`.

`POST /api/users/upsert` идемпотентен по `telegram_id`: сценарий
`UpsertUser` обновляет профильные поля, но не трогает `is_active`
(забаненный юзер не «разбанивается» ботом). Гостей (telegram_id ≤ 0)
бот не создаёт — это канал веб-регистрации.
"""

from app.transport.http.deps.dishka import InternalDishkaRoute

import logging

from fastapi import APIRouter, HTTPException, status

from app.application.errors import AppError
from app.application.users.use_cases import UpsertUserCommand
from app.transport.http.deps.use_cases import UpsertUserDep
from app.transport.http.schemas.user import UserOut, UserUpsert

logger = logging.getLogger(__name__)

router = APIRouter(route_class=InternalDishkaRoute,
    prefix="/api/users",
    tags=["users"],
)


@router.post("/upsert", response_model=UserOut)
async def users_upsert(body: UserUpsert, use_case: UpsertUserDep) -> UserOut:
    """Создать/обновить пользователя по данным из Telegram (для бота).

    Идемпотентно: повторный вызов с тем же `telegram_id` обновляет
    профиль, не создаёт дубль. `is_active` существующего пользователя
    **не** меняется — бан не снимается через upsert.

    Args:
        body: Профиль из Telegram.
        use_case: Сценарий upsert (событие `user_registered` — только при
            первой регистрации).

    Returns:
        `UserOut`.

    Raises:
        HTTPException: 422 если `telegram_id ≤ 0` (бот не создаёт гостей).
    """
    # Бот работает только с реальными telegram_id. Гости (id < 0, email=None)
    # создаются через POST /api/auth/guest — не через bot-канал.
    if body.telegram_id <= 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="telegram_id must be > 0 (bot does not create guests)",
        )

    try:
        profile = await use_case.execute(
            UpsertUserCommand(
                telegram_id=body.telegram_id,
                username=body.username,
                first_name=body.first_name,
                last_name=body.last_name,
                language_code=body.language_code,
                source="bot",
            )
        )
    except AppError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=exc.public_message
        ) from exc

    logger.info(
        "user_upserted telegram_id=%s source=bot is_active=%s",
        profile.telegram_id, profile.is_active,
    )
    return UserOut.model_validate(profile)
