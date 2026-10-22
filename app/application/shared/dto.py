"""DTO прикладного слоя (dataclass, не Pydantic)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class FavoriteState:
    """Состояние избранного после toggle."""

    favorited: bool


@dataclass(frozen=True, slots=True)
class CurrentUser:
    """Аутентифицированный субъект (без ORM-полей)."""

    id: int
    role: str
    is_active: bool = True
