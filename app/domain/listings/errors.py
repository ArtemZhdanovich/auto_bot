"""Ошибки контекста объявлений."""

from __future__ import annotations

from app.domain.shared.errors import NotFound


class ListingNotFound(NotFound):
    """Объявление не существует или снято с публикации."""

    code = "listing_not_found"

    def __init__(self, listing_id: int) -> None:
        self.listing_id = listing_id
        super().__init__("Listing not found")
