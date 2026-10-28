"""Превью ссылок в чатах: внутренние карточки лотов и внешние OG-теги (§5).

Внутренний паттерн `/listing/{id}` (§8) отдаёт карточку из БД; внешние
ссылки грузит `LinkFetcherPort` (SSRF-фильтр — в инфраструктуре), OG-теги
парсятся регуляркой, результат кэшируется в Redis на 24 часа.
"""

from __future__ import annotations

import html
import json
import logging
import re
from hashlib import md5
from typing import Any
from urllib.parse import urlparse

from app.application.catalog.ports import ListingReadRepository
from app.application.chat.ports import LinkFetcherPort
from app.application.errors import NotFound
from app.application.shared.ports import CachePort

logger = logging.getLogger(__name__)

# Внутренние ссылки на PDP (§8): /listing/{id} — с любым хостом/префиксом.
_LISTING_PATH_RE = re.compile(r"/listing/(\d+)")

# TTL кэша превью (§5: Redis 24ч).
PREVIEW_TTL_SEC = 24 * 60 * 60

# OG-теги: property="og:...", значения экранированы HTML-сущностями.
_OG_RE = re.compile(
    r"<meta[^>]+property=[\"']og:(title|description|image|site_name)[\"']"
    r"[^>]+content=[\"']([^\"']*)[\"']",
    re.IGNORECASE,
)
_OG_RE_REVERSED = re.compile(
    r"<meta[^>]+content=[\"']([^\"']*)[\"'][^>]+"
    r"property=[\"']og:(title|description|image|site_name)[\"']",
    re.IGNORECASE,
)


def parse_og(html_text: str, url: str) -> dict[str, Any]:
    """Вытащить og:title/description/image/site_name из HTML-головы.

    Плейсхолдер при отсутствии тегов: домен-подпись, пустые поля (§8).
    """
    tags: dict[str, str] = {}
    for match in _OG_RE.finditer(html_text):
        tags[match.group(1)] = html.unescape(match.group(2))
    for match in _OG_RE_REVERSED.finditer(html_text):
        tags.setdefault(match.group(2), html.unescape(match.group(1)))
    domain = urlparse(url).hostname or ""
    return {
        "kind": "external",
        "title": tags.get("title") or domain,
        "description": tags.get("description") or "",
        "image": tags.get("image") or None,
        "site_name": tags.get("site_name") or domain,
        "domain": domain,
        "url": url,
    }


class GetLinkPreview:
    """GET /api/chats/{id}/link-preview — карточка ссылки для сообщения."""

    def __init__(
        self,
        listings: ListingReadRepository,
        fetcher: LinkFetcherPort,
        cache: CachePort,
    ) -> None:
        self._listings = listings
        self._fetcher = fetcher
        self._cache = cache

    async def execute(self, url: str) -> dict[str, Any]:
        """Превью ссылки: карточка лота или OG-теги внешней страницы.

        Raises:
            NotFound: Внутренняя ссылка на несуществующий/неактивный лот.
        """
        url = url.strip()
        internal = _LISTING_PATH_RE.search(url)
        if internal is not None:
            return await self._listing_preview(int(internal.group(1)))

        key = f"chat:preview:{md5(url.encode()).hexdigest()}"
        cached = await self._cache.get(key)
        if cached is not None:
            try:
                return json.loads(cached)
            except (TypeError, ValueError):
                logger.warning("preview_cache_corrupted key=%s", key)

        # Ошибки SSRF-фильтра (LinkBlockedError) уходят наверх → 400;
        # недоступная страница (None) — плейсхолдер, а не ошибка.
        html_text = await self._fetcher.fetch(url)
        preview = (
            parse_og(html_text, url)
            if html_text is not None
            else {"kind": "external", "domain": urlparse(url).hostname or "",
                  "title": urlparse(url).hostname or url, "description": "",
                  "image": None, "site_name": "", "url": url}
        )
        await self._cache.set(key, json.dumps(preview), ttl_sec=PREVIEW_TTL_SEC)
        return preview

    async def _listing_preview(self, listing_id: int) -> dict[str, Any]:
        """Карточка активного лота: фото/модель/цена/ссылка (§5)."""
        card = await self._listings.get_active(listing_id)
        if card is None:
            raise NotFound("listing_not_found: лот недоступен")
        image = card.images[0] if card.images else None
        return {
            "kind": "listing",
            "id": card.id,
            "make": card.make,
            "model": card.model,
            "year": card.year,
            "price": float(card.price) if card.price is not None else None,
            "currency": card.currency,
            "mileage_km": card.mileage_km,
            "url": card.url,
            "image": image,
            "has_market": True,
        }
