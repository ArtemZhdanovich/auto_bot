"""Сценарии чтения объявлений: лента, facets, by-url, PDP, осмотр.

Модуль не знает про FastAPI, SQLAlchemy и конкретные адаптеры: только
DTO, порты и прикладные ошибки. Порт объявлений объявлен здесь же, чтобы
не плодить файлы: реализация — `SqlAlchemyListingReadRepository`.
"""

from __future__ import annotations

import base64
import json
import logging
import re
from datetime import datetime

from app.application.catalog.dto import (
    FeedCursor,
    InspectionUpdate,
    InspectionView,
    ListingCard,
    ListingDetail,
    ListingFeedQuery,
    ListingPageResult,
)
from app.application.catalog.ports import ListingReadRepository
from app.application.errors import Conflict, InvalidInput, NotFound
from app.application.shared.pagination import CursorCodec
from app.application.shared.ports import UnitOfWork

logger = logging.getLogger(__name__)

_BY_URL_MIN_NEEDLE_LEN = 10


def normalize_url(url: str) -> str:
    """Нормализация ссылки: без схемы, www/m., финального слеша."""
    u = url.strip().lower()
    u = re.sub(r"^https?://", "", u)
    u = re.sub(r"^(www|m)\.", "", u)
    return u.rstrip("/")


def decode_feed_cursor(raw: str) -> FeedCursor:
    """Разобрать курсор ленты (`CursorCodec.encode`).

    Raises:
        InvalidInput: 422-совместимая ошибка, если курсор не base64/JSON,
            без нужных полей или с naive `parsed_at`.
    """
    try:
        decoded = base64.urlsafe_b64decode(raw.encode("ascii"))
        data = json.loads(decoded)
        parsed_at = datetime.fromisoformat(data["parsed_at"])
        listing_id = int(data["id"])
    except (ValueError, KeyError, TypeError) as exc:
        # binascii.Error и UnicodeError — подклассы ValueError.
        raise InvalidInput("Invalid cursor") from exc
    if parsed_at.tzinfo is None:
        raise InvalidInput("Invalid cursor: parsed_at must include timezone")
    return FeedCursor(parsed_at=parsed_at, listing_id=listing_id)


def _count_user_filters(query: ListingFeedQuery) -> int:
    """Сколько фильтров задал пользователь (для analytics)."""
    values = (
        query.source,
        query.make,
        query.model,
        query.year_from,
        query.year_to,
        query.price_min,
        query.price_max,
        query.currency,
        query.mileage_min,
        query.mileage_max,
        query.body_type,
        query.fuel,
        query.transmission,
        query.drive,
        query.color,
        query.engine_volume_min,
        query.engine_volume_max,
    )
    return sum(1 for value in values if value is not None)


def _validate(query: ListingFeedQuery) -> None:
    """Cross-field валидация: пустой результат при инверсии — плохой UX.

    Raises:
        InvalidInput: Если `cursor` передан не с `sort=new` или границы
            диапазона перевёрнуты.
    """
    if query.cursor is not None and query.sort != "new":
        raise InvalidInput("cursor is supported only with sort=new")
    if (
        query.year_from is not None
        and query.year_to is not None
        and query.year_from > query.year_to
    ):
        raise InvalidInput("year_from > year_to")
    if (
        query.price_min is not None
        and query.price_max is not None
        and query.price_min > query.price_max
    ):
        raise InvalidInput("price_min > price_max")
    if (
        query.mileage_min is not None
        and query.mileage_max is not None
        and query.mileage_min > query.mileage_max
    ):
        raise InvalidInput("mileage_min > mileage_max")
    if (
        query.engine_volume_min is not None
        and query.engine_volume_max is not None
        and query.engine_volume_min > query.engine_volume_max
    ):
        raise InvalidInput("engine_volume_min > engine_volume_max")


class ListListings:
    """Лента с фильтрами, сортировкой и keyset-пагинацией."""

    def __init__(self, listings: ListingReadRepository) -> None:
        self._listings = listings

    async def execute(self, query: ListingFeedQuery) -> ListingPageResult:
        """Вернуть страницу ленты.

        В keyset-режиме (`cursor`) `total` = 0: `count(*)` не выполняется.
        `next_cursor` отдаётся только для `sort=new`.

        Raises:
            InvalidInput: Некорректный курсор или перевёрнутые границы.
        """
        _validate(query)
        cursor = decode_feed_cursor(query.cursor) if query.cursor else None

        items, total, has_more = await self._listings.page(query, cursor)

        next_cursor: str | None = None
        if query.sort == "new" and has_more and items:
            last = items[-1]
            next_cursor = CursorCodec.encode(
                {"parsed_at": last.parsed_at.isoformat(), "id": last.id}
            )

        user_filters = _count_user_filters(query)
        if user_filters > 0:
            # track — best-effort: сбой analytics не должен ронять ленту.
            try:
                await self._listings.record_search(
                    None,
                    results=len(items) if cursor is not None else int(total or 0),
                    filters=user_filters,
                )
            except Exception:  # noqa: BLE001
                logger.exception("search_track_failed")

        return ListingPageResult(
            total=0 if cursor is not None else int(total or 0),
            items=items,
            next_cursor=next_cursor,
            has_more=has_more,
        )


class ListListingFacets:
    """Справочник марок/моделей для зависимых селектов."""

    def __init__(self, listings: ListingReadRepository) -> None:
        self._listings = listings

    async def execute(self, make: str | None) -> dict[str, list[str]]:
        """Вернуть `{"makes": [...]}` или `{"models": [...]}`."""
        return await self._listings.facets(make)


class GetListingByUrl:
    """Объявление по ссылке (бот: «скинь ссылку, покажу карточку»)."""

    def __init__(self, listings: ListingReadRepository) -> None:
        self._listings = listings

    async def execute(self, url: str) -> ListingCard:
        """Найти объявление по нормализованному хвосту URL.

        Raises:
            InvalidInput: Ссылка слишком короткая для поиска.
            NotFound: Объявление не найдено.
            Conflict: По ссылке нашлось больше одного объявления.
        """
        needle = normalize_url(url)
        if len(needle) < _BY_URL_MIN_NEEDLE_LEN:
            raise InvalidInput("URL is too short to search")
        rows = await self._listings.find_by_url(needle)
        if not rows:
            raise NotFound("Listing not found")
        if len(rows) > 1:
            logger.warning(
                "listing_by_url_ambiguous needle=%r count=%d", needle, len(rows)
            )
            raise Conflict("Ambiguous URL, use listing id")
        return rows[0]


class GetListing:
    """Карточка объявления (PDP) с историей цен."""

    def __init__(self, listings: ListingReadRepository) -> None:
        self._listings = listings

    async def execute(self, listing_id: int) -> ListingDetail:
        """Вернуть карточку и историю цен.

        `enrich` — best-effort: таймаут источника не блокирует ответ.

        Raises:
            NotFound: Объявления нет или оно неактивно.
        """
        card = await self._listings.get_active(listing_id)
        if card is None:
            raise NotFound("Listing not found")
        try:
            enriched = await self._listings.enrich(listing_id)
        except Exception:  # noqa: BLE001
            logger.warning("enrich_failed listing_id=%s", listing_id, exc_info=True)
        else:
            card = enriched or card
        history = await self._listings.price_history(listing_id)
        return ListingDetail(card=card, price_history=history)


class GetInspection:
    """Отчёт осмотра для PDP."""

    def __init__(self, listings: ListingReadRepository) -> None:
        self._listings = listings

    async def execute(self, listing_id: int) -> InspectionView:
        """Вернуть отчёт осмотра.

        Raises:
            NotFound: Объявления нет/оно неактивно или осмотра нет.
        """
        if await self._listings.get_active(listing_id) is None:
            raise NotFound("Listing not found")
        report = await self._listings.get_report(listing_id)
        if report is None:
            raise NotFound("Inspection not found")
        return report


class UpsertInspection:
    """Создать/заменить отчёт осмотра (только admin — проверка в transport)."""

    def __init__(
        self, uow: UnitOfWork, listings: ListingReadRepository
    ) -> None:
        self._uow = uow
        self._listings = listings

    async def execute(
        self, listing_id: int, update: InspectionUpdate
    ) -> InspectionView:
        """Сохранить отчёт; неактивные объявления разрешены.

        Raises:
            NotFound: Объявления нет.
        """
        async with self._uow:
            if not await self._listings.exists(listing_id):
                raise NotFound("Listing not found")
            view = await self._listings.upsert_report(listing_id, update)
            await self._uow.commit()
        return view
