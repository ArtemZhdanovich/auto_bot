"""Избранное: тонкий транспортный слой.

Ни SQL, ни бизнес-правил: только разбор параметров, вызов use case и
маппинг результата в Pydantic-схему.
"""


from __future__ import annotations
from app.transport.http.deps.dishka import DishkaRoute

import logging
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query, status
from pydantic import BaseModel

from app.application.catalog.dto import (
    ListFavoritesQuery,
    ToggleFavoriteCommand,
)
from app.application.shared.pagination import CursorCodec
from app.application.errors import NotFound
from app.transport.http.deps.auth import CurrentUserDep
from app.transport.http.deps.use_cases import (
    IsFavoriteDep,
    ListFavoriteCardsDep,
    RemoveFavoriteDep,
    ToggleFavoriteDep,
)
from app.transport.http.schemas.listing import ListingOut

logger = logging.getLogger(__name__)

router = APIRouter(route_class=DishkaRoute, prefix="/api", tags=["favorites"])


class FavoriteStatusOut(BaseModel):
    """Статус избранного для объявления."""

    favorited: bool


def _decode_cursor(raw: str | None):
    """Разобрать курсор или вернуть 400 (невалидный ввод)."""
    if raw is None:
        return None
    try:
        return CursorCodec.decode_favorite(raw)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid cursor",
        ) from exc


@router.get("/favorites", response_model=list[ListingOut])
async def list_favorites(
    user: CurrentUserDep,
    use_case: ListFavoriteCardsDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    cursor: str | None = None,
) -> list[ListingOut]:
    """Избранные объявления пользователя (новые сверху).

    `cursor` — keyset-продолжение; при его наличии `offset` игнорируется.
    """
    cards = await use_case.execute(
        ListFavoritesQuery(
            user_id=user.telegram_id,
            limit=limit,
            offset=offset,
            cursor=_decode_cursor(cursor),
        )
    )
    return [ListingOut.model_validate(card) for card in cards]


@router.get("/favorites/{listing_id}", response_model=FavoriteStatusOut)
async def is_favorite(
    user: CurrentUserDep,
    use_case: IsFavoriteDep,
    listing_id: Annotated[int, Path(ge=1)],
) -> FavoriteStatusOut:
    """Проверить, добавлено ли объявление в избранное."""
    try:
        favorited = await use_case.execute(user.telegram_id, listing_id)
    except NotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Listing not found"
        ) from exc
    return FavoriteStatusOut(favorited=favorited)


@router.post("/listings/{listing_id}/favorite", response_model=FavoriteStatusOut)
async def toggle_favorite(
    user: CurrentUserDep,
    use_case: ToggleFavoriteDep,
    listing_id: Annotated[int, Path(ge=1)],
) -> FavoriteStatusOut:
    """Toggle «В избранное» (атомарность — в репозитории)."""
    try:
        state = await use_case.execute(
            ToggleFavoriteCommand(
                user_id=user.telegram_id, listing_id=listing_id
            )
        )
    except NotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Listing not found"
        ) from exc
    return FavoriteStatusOut(favorited=state.favorited)


@router.delete(
    "/listings/{listing_id}/favorite",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_favorite(
    user: CurrentUserDep,
    use_case: RemoveFavoriteDep,
    listing_id: Annotated[int, Path(ge=1)],
) -> None:
    """Удалить из избранного (идемпотентно)."""
    await use_case.execute(user.telegram_id, listing_id)
