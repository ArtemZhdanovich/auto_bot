"""Профиль пользователя: GET /api/me (ТЗ п. 4.3)."""

from fastapi import APIRouter

from app.core.deps import CurrentUserDep
from app.schemas.user import UserOut

router = APIRouter(prefix="/api", tags=["users"])


@router.get("/me", response_model=UserOut)
async def me(user: CurrentUserDep) -> UserOut:
    return UserOut.model_validate(user)
