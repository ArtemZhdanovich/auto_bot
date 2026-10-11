"""Che168HtmlAdapter — источник che168.com (Китай, SSR-парсинг HTML).

Che168 (二手车之家, Autohome) отдаёт серверный HTML со списком объявлений.
Адаптер парсит его selectolax'ом и приводит к UnifiedListing:

- цены в формате «23.80万» → 238000 CNY (1万 = 10 000);
- пробег «5.2万公里» → 52000 км;
- марка извлекается из заголовка иероглифами (丰田 → Toyota) через MAKE_MAP;
- VIN в Китае обычно скрыт → дедуп по (source, source_id) + fuzzy по полям
  выполняется в общем конвейере (app/adapters/common/dedup.py).

Селекторы и пути — в `sources.config` (ТЗ п. 6.1: код в Git, конфиг в БД):
при смене вёрстки правится БД без деплоя. Селекторы по умолчанию
соответствуют списку `ul#usedList li`, но должны быть сверены с живым HTML
перед включением источника (sources.enabled=true) — см. PROGRESS.md.
"""

import logging
import re
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urljoin

from selectolax.lexbor import LexborHTMLParser as HTMLParser

from app.adapters.base import BaseApiAdapter
from app.adapters.common.normalize import (
    MAKE_MAP,
    detect_fuel,
    detect_transmission,
)
from app.schemas.criteria import SearchCriteria
from app.schemas.listing import FetchResult, UnifiedListing

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://www.che168.com"
DEFAULT_LIST_PATH = "/china/a0_0msdgscncgpi1ltocsp1exb0/"
DEFAULT_CARD_SELECTOR = "ul#usedList li"
DEFAULT_TITLE_SELECTOR = "h4"
DEFAULT_PRICE_SELECTOR = ".price"
DEFAULT_LINK_SELECTOR = "a"
DEFAULT_MAX_PAGES = 5

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9",
}

# CJK-диапазон — чтобы отличать иероглифические ключи справочника от латиницы
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
# ID объявления из ссылки вида /a/71085432/ или /usedcar/71085432.html
_ID_RE = re.compile(r"/(?:a|usedcar)/(\d+)")
# «23.80万» / «238000» (万 = 10 000)
_PRICE_RE = re.compile(r"([\d.]+)\s*(万?)")
# «5.2万公里» / «52300公里»
_MILEAGE_RE = re.compile(r"([\d.]+)\s*(万?)公里")
# «2020年上牌», «2020款»
_YEAR_RE = re.compile(r"(20\d{2})\s*[年款]")

# CJK-ключи MAKE_MAP, длинные раньше (广汽传祺 проверяется до 传祺)
_CJK_MAKES: list[tuple[str, str]] = sorted(
    ((k, v) for k, v in MAKE_MAP.items() if _CJK_RE.search(k)),
    key=lambda kv: len(kv[0]),
    reverse=True,
)


def extract_make_zh(text: str | None) -> str | None:
    """Поиск марки иероглифами в тексте: «2020款 丰田汉兰达…» → 'Toyota'."""
    if not text:
        return None
    for key, make in _CJK_MAKES:
        if key in text:
            return make
    return None


def parse_wan_number(raw: str | None, regex: re.Pattern[str]) -> Decimal | None:
    """«23.80万» → 238000, «52300» → 52300; None, если числа нет."""
    if not raw:
        return None
    match = regex.search(raw)
    if not match:
        return None
    try:
        value = Decimal(match.group(1))
    except InvalidOperation:
        return None
    if match.group(2) == "万":  # 万 = 10 000
        value *= 10_000
    return value


class Che168HtmlAdapter(BaseApiAdapter):
    """SSR-парсер che168.com (kind='parser' в таблице `sources`)."""

    source_name = "che168"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._base_url = self.config.get("base_url", DEFAULT_BASE_URL).rstrip("/")
        self._list_path = self.config.get("list_path", DEFAULT_LIST_PATH)
        self._card_selector = self.config.get("card_selector", DEFAULT_CARD_SELECTOR)
        self._title_selector = self.config.get("title_selector", DEFAULT_TITLE_SELECTOR)
        self._price_selector = self.config.get("price_selector", DEFAULT_PRICE_SELECTOR)
        self._link_selector = self.config.get("link_selector", DEFAULT_LINK_SELECTOR)
        self._max_pages = int(self.config.get("max_pages", DEFAULT_MAX_PAGES))
        # capabilities.fuel_from_text / transmission_from_text — искать в тексте карточки
        self._fuel_from_text = bool(self.capabilities.get("fuel_from_text", True))
        self._transmission_from_text = bool(
            self.capabilities.get("transmission_from_text", True)
        )

    # --- fetch ---------------------------------------------------------

    async def fetch(
        self, criteria: SearchCriteria, cursor: str | None = None
    ) -> FetchResult:
        page = int(cursor) if cursor else 1
        params = {"page": str(page)} if page > 1 else None
        resp = await self.http.get(
            self._base_url + self._list_path, params=params, headers=HEADERS
        )
        items = self._parse(resp.text)

        has_more = bool(items) and page < self._max_pages
        return FetchResult(
            items=items,
            next_cursor=str(page + 1) if has_more else None,
            has_more=has_more,
            source_meta={"page": page, "parsed": len(items)},
        )

    async def healthcheck(self) -> bool:
        """200 + контейнер списка = источник жив (вёрстка не сломана)."""
        try:
            resp = await self.http.get(
                self._base_url + self._list_path, headers=HEADERS
            )
        except Exception:
            return False
        return bool(HTMLParser(resp.text).css(self._card_selector))

    # --- parsing -------------------------------------------------------

    def _parse(self, html: str) -> list[UnifiedListing]:
        tree = HTMLParser(html)
        items: list[UnifiedListing] = []
        for card in tree.css(self._card_selector):
            try:
                listing = self._map_card(card)
            except Exception:  # noqa: BLE001 — битая карточка не роняет страницу
                logger.warning("che168_card_parse_error", exc_info=True)
                continue
            if listing is not None:
                items.append(listing)
        return items

    def _map_card(self, card) -> UnifiedListing | None:
        link = card.css_first(self._link_selector)
        href = (link.attributes.get("href") if link else None) or ""
        source_id = self._extract_source_id(card, link, href)
        if source_id is None:
            return None

        title_node = card.css_first(self._title_selector)
        title = title_node.text(strip=True) if title_node else None
        text = card.text(separator=" ", strip=True)

        price_node = card.css_first(self._price_selector)
        price_text = price_node.text(strip=True) if price_node else None
        price = parse_wan_number(price_text or text, _PRICE_RE)

        mileage_match = _MILEAGE_RE.search(text)
        mileage_km = None
        if mileage_match:
            value = Decimal(mileage_match.group(1))
            if mileage_match.group(2) == "万":
                value *= 10_000
            mileage_km = int(value)

        year_match = _YEAR_RE.search(text)
        year = int(year_match.group(1)) if year_match else None

        return UnifiedListing(
            source=self.source_name,
            source_id=source_id,
            url=urljoin(self._base_url + "/", href),
            title=title,
            make=extract_make_zh(title) or extract_make_zh(text),
            model=None,  # модели иероглифами (汉兰达) — справочник моделей отдельным шагом
            year=year,
            price=price,
            currency="CNY",
            mileage_km=mileage_km,
            vin=None,  # в Китае VIN обычно не публикуется
            fuel=detect_fuel(text) if self._fuel_from_text else None,
            transmission=(
                detect_transmission(text) if self._transmission_from_text else None
            ),
            seller_type="dealer",
            images=self._extract_images(card),
            # raw_payload для перепарсинга при смене селекторов (ТЗ п. 6.1)
            raw={"title": title, "price_text": price_text, "html": card.html[:2000]},
        )

    def _extract_source_id(self, card, link, href: str) -> str | None:
        # 1) data-id на карточке/ссылке, 2) цифры из ссылки /a/{id}/
        data_id = card.attributes.get("data-id")
        if not data_id and link is not None:
            data_id = link.attributes.get("data-id")
        if data_id:
            return str(data_id)
        match = _ID_RE.search(href or "")
        return match.group(1) if match else None

    def _extract_images(self, card) -> list[str]:
        urls: list[str] = []
        for img in card.css("img"):
            src = img.attributes.get("data-src") or img.attributes.get("src") or ""
            if src and not src.startswith("data:"):  # пропускаем lazy-placeholder
                urls.append(urljoin(self._base_url + "/", src))
        return urls
