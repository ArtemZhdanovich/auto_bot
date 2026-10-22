"""Курсоры и страницы для keyset-пагинации."""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Generic, TypeVar

from app.domain.favorites.ports import FavoriteCursor

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class Page(Generic[T]):
    """Страница результатов.

    Attributes:
        items: Элементы текущей страницы.
        has_more: Есть ли следующая страница.
        next_cursor: Непрозрачный курсор для следующего запроса.
    """

    items: list[T]
    has_more: bool
    next_cursor: str | None = None


class CursorCodec:
    """Кодирование курсора в base64(JSON).

    Курсор непрозрачен для клиента, но не является защитой: его содержимое
    (время + id) не секрет. Задача — не давать клиенту собирать SQL-условия
    вручную и валидировать формат на входе.
    """

    @staticmethod
    def encode(payload: dict) -> str:
        """Сериализовать payload в URL-safe base64."""
        raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
        return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")

    @staticmethod
    def decode_favorite(raw: str) -> FavoriteCursor:
        """Разобрать курсор избранного.

        Raises:
            ValueError: Если курсор не base64/JSON или в нём нет полей.
        """
        try:
            decoded = base64.urlsafe_b64decode(raw.encode("ascii"))
            data = json.loads(decoded)
            return FavoriteCursor(
                created_at=datetime.fromisoformat(data["created_at"]),
                listing_id=int(data["listing_id"]),
            )
        except (ValueError, KeyError, TypeError, UnicodeDecodeError) as exc:
            raise ValueError("Invalid cursor") from exc
