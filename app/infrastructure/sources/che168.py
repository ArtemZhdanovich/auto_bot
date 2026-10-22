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

`fetch(criteria, cursor)` **не применяет `criteria`**: che168 не поддерживает
фильтры в URL списка, фильтрация выполняется downstream (pipeline/FilterService).
Если это изменится — реализовать клиентский фильтр перед возвратом `FetchResult`.
"""

import logging
import re
from decimal import Decimal, InvalidOperation
from types import MappingProxyType
from typing import Any
from urllib.parse import urljoin, urlparse

from selectolax.lexbor import LexborHTMLParser as HTMLParser

from app.infrastructure.sources.base import BaseApiAdapter, FetchError
from app.infrastructure.sources.common.normalize import (
    MAKE_MAP,
    detect_fuel,
    detect_transmission,
)
from app.domain.search.criteria import SearchCriteria
from app.domain.listings.unified import FetchResult, UnifiedListing

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://www.che168.com"
DEFAULT_LIST_PATH = "/china/a0_0msdgscncgpi1ltocsp1exb0/"
DEFAULT_CARD_SELECTOR = "ul#usedList li"
DEFAULT_TITLE_SELECTOR = "h4"
DEFAULT_PRICE_SELECTOR = ".price"
DEFAULT_LINK_SELECTOR = "a"
DEFAULT_MAX_PAGES = 5

# mappingproxy: никто случайно не мутирует общий HEADERS в процессе.
HEADERS = MappingProxyType(
    {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9",
        # Анти-боты (в т.ч. che168) ожидают Referer на свой же сайт.
        "Referer": DEFAULT_BASE_URL + "/",
    }
)

# CJK-диапазон — чтобы отличать иероглифические ключи справочника от латиницы
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
# ID объявления из ссылки вида /a/71085432/ или /usedcar/71085432.html
_ID_RE = re.compile(r"/(?:a|usedcar)/(\d+)")
# Цена с 万: «23.80万», «1500.00万». Требуется 万 — иначе «2020款» матчится как цена.
_PRICE_WAN_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(万)")
# Голое число для price_node (если сайт когда-нибудь отдаст «238000» без 万).
_PRICE_PLAIN_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*$")
# Backward-compat алиас для внешних вызовов parse_wan_number(raw, _PRICE_RE).
_PRICE_RE = _PRICE_WAN_RE
# Пробег: «5.2万公里» / «52300公里»
_MILEAGE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(万?)公里")
# Год: «2020年上牌», «2020款». Первое совпадение берётся как есть;
# при «2018款 ... 2020年上牌» вернёт 2018 (модельный) — см. докстринг класса.
_YEAR_RE = re.compile(r"(20\d{2})\s*[年款]")

# Схемы, запрещённые в href/src из чужого HTML.
_FORBIDDEN_SCHEMES = frozenset({"javascript", "data", "vbscript", "file"})

# CJK-ключи MAKE_MAP, длинные раньше (广汽传祺 проверяется до 传祺)
_CJK_MAKES: list[tuple[str, str]] = sorted(
    ((k, v) for k, v in MAKE_MAP.items() if _CJK_RE.search(k)),
    key=lambda kv: len(kv[0]),
    reverse=True,
)


def _safe_join(base: str, href: str | None) -> str | None:
    """`urljoin` с валидацией схемы и финального URL.

    `urljoin("https://a/", "javascript:alert(1)")` возвращает
    `javascript:alert(1)` — это XSS-вектор при рендере `<a href>`.
    `//evil.com/x` → `https://evil.com/x` — трекинг-пиксель. Отсекаем.

    Args:
        base: Базовый URL (с trailing slash).
        href: Относительный или абсолютный URL из HTML.

    Returns:
        Безопасный абсолютный http(s)-URL или `None`.
    """
    if not href:
        return None
    href = href.strip()
    scheme = urlparse(href).scheme.lower()
    if scheme in _FORBIDDEN_SCHEMES:
        return None
    joined = urljoin(base, href)
    parsed = urlparse(joined)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return None
    # Только свой домен (и его поддомены): `//evil.com` иначе даёт
    # внешний URL, который уходит в БД и в карточку.
    base_host = (urlparse(base).hostname or "").lower()
    host = (parsed.hostname or "").lower()
    root = ".".join(base_host.split(".")[-2:]) if base_host else ""
    if not root or not (host == root or host.endswith("." + root)):
        return None
    return joined


def extract_make_zh(text: str | None) -> str | None:
    """Поиск марки иероглифами в тексте: «2020款 丰田汉兰达…» → 'Toyota'.

    Args:
        text: Текст карточки или заголовок.

    Returns:
        Каноническое имя марки или `None`, если ни одна не найдена.
    """
    if not text:
        return None
    for key, make in _CJK_MAKES:
        if key in text:
            return make
    return None


def parse_wan_number(
    raw: str | None, regex: re.Pattern[str]
) -> Decimal | None:
    """«23.80万» → 238000, «52300» → 52300; None, если числа нет.

    Args:
        raw: Строка с числом.
        regex: Скомпилированный regex с двумя группами: число и опциональный «万».

    Returns:
        Число как `Decimal` или `None`.
    """
    if not raw:
        return None
    match = regex.search(raw)
    if not match:
        return None
    try:
        value = Decimal(match.group(1))
    except InvalidOperation:
        return None
    if len(match.groups()) >= 2 and match.group(2) == "万":  # 万 = 10 000
        value *= 10_000
    return value


class Che168HtmlAdapter(BaseApiAdapter):
    """SSR-парсер che168.com (kind='parser' в таблице `sources`).

    Особенности:
    - `fetch` не применяет `criteria` — фильтрация downstream;
    - `_max_pages` ограничивает пагинацию (клампится к >= 1);
    - URL из HTML (`href`, `img.src`) валидируются по схеме (http/https);
    - при сбое `price_selector` цена извлекается только из «X万»
      (иначе год выпуска матчился бы как цена).

    Attributes:
        source_name: `"che168"`.
    """

    source_name = "che168"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._base_url = self.config.get("base_url", DEFAULT_BASE_URL).rstrip("/")
        self._list_path = self.config.get("list_path", DEFAULT_LIST_PATH)
        self._card_selector = self.config.get("card_selector", DEFAULT_CARD_SELECTOR)
        self._title_selector = self.config.get("title_selector", DEFAULT_TITLE_SELECTOR)
        self._price_selector = self.config.get("price_selector", DEFAULT_PRICE_SELECTOR)
        self._link_selector = self.config.get("link_selector", DEFAULT_LINK_SELECTOR)
        try:
            self._max_pages = max(1, int(self.config.get("max_pages", DEFAULT_MAX_PAGES)))
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"che168: invalid max_pages={self.config.get('max_pages')!r}"
            ) from exc
        # capabilities.fuel_from_text / transmission_from_text — искать в тексте карточки
        self._fuel_from_text = bool(self.capabilities.get("fuel_from_text", True))
        self._transmission_from_text = bool(
            self.capabilities.get("transmission_from_text", True)
        )

    # --- fetch ---------------------------------------------------------

    async def fetch(
        self, criteria: SearchCriteria, cursor: str | None = None
    ) -> FetchResult:
        """Забирает страницу объявлений che168.

        `criteria` не применяется: che168 не поддерживает фильтры в URL
        списка; фильтрация — downstream. См. докстринг модуля.

        Args:
            criteria: Критерии поиска (игнорируются).
            cursor: Номер страницы строкой (`"1"`, `"2"`, …) или `None`.

        Returns:
            `FetchResult` с объявлениями и `next_cursor` (или `None`).

        Raises:
            FetchError: При невалидном `cursor` или ошибке HTTP.
        """
        page = self._parse_cursor(cursor)
        params = {"page": str(page)} if page > 1 else None
        resp = await self.http.get(
            self._base_url + self._list_path, params=params, headers=dict(HEADERS)
        )
        items = self._parse(resp.text)

        if page > 1 and not items:
            # Аномалия: либо конец пагинации, либо `?page=N` не поддерживается
            # (che168 исторически пагинирует через сегмент пути `sp2`, `sp3`).
            logger.warning(
                "che168_empty_page page=%d url=%s (pagination may be broken)",
                page,
                resp.url,
            )

        has_more = bool(items) and page < self._max_pages
        return FetchResult(
            items=items,
            next_cursor=str(page + 1) if has_more else None,
            has_more=has_more,
            source_meta={"page": page, "parsed": len(items)},
        )

    @staticmethod
    def _parse_cursor(cursor: str | None) -> int:
        """Валидирует курсор и возвращает номер страницы.

        Args:
            cursor: Строка курсора или `None` (первая страница).

        Returns:
            Номер страницы (>= 1).

        Raises:
            FetchError: Если курсор не целое положительное число.
        """
        if not cursor:
            return 1
        try:
            page = int(cursor)
        except (TypeError, ValueError) as exc:
            raise FetchError(f"che168: invalid cursor {cursor!r}") from exc
        if page < 1:
            raise FetchError(f"che168: cursor must be >= 1, got {page}")
        return page

    async def healthcheck(self) -> bool:
        """200 + контейнер списка = источник жив (вёрстка не сломана).

        Returns:
            `True`, если в ответе есть хотя бы одна карточка по текущему
            `card_selector`; `False` при ошибке HTTP или пустом списке.
        """
        try:
            resp = await self.http.get(
                self._base_url + self._list_path, headers=dict(HEADERS)
            )
        except Exception:  # noqa: BLE001 — healthcheck не должен ронять вызывающего
            return False
        return bool(HTMLParser(resp.text).css(self._card_selector))

    # --- parsing -------------------------------------------------------

    def _parse(self, html: str) -> list[UnifiedListing]:
        """Парсит HTML списка; битые карточки пропускаются с warning.

        Args:
            html: HTML страницы списка.

        Returns:
            Список `UnifiedListing`.
        """
        tree = HTMLParser(html)
        items: list[UnifiedListing] = []
        for card in tree.css(self._card_selector):
            try:
                listing = self._map_card(card)
            except Exception:  # noqa: BLE001 — битая карточка не роняет страницу
                logger.warning(
                    "che168_card_parse_error source=%s href=%s",
                    self.source_name,
                    (card.css_first(self._link_selector).attributes.get("href")
                     if card.css_first(self._link_selector) else None),
                    exc_info=True,
                )
                continue
            if listing is not None:
                items.append(listing)
        return items

    def _map_card(self, card) -> UnifiedListing | None:
        """Преобразует одну карточку в `UnifiedListing` или `None`.

        Args:
            card: `selectolax`-узел карточки.

        Returns:
            `UnifiedListing` или `None`, если не удалось извлечь `source_id`.
        """
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
        price = self._extract_price(price_text, text)

        mileage_km = self._extract_mileage(text)

        year_match = _YEAR_RE.search(text)
        year = int(year_match.group(1)) if year_match else None

        # URL и картинки из чужого HTML — валидируем схему.
        url = _safe_join(self._base_url + "/", href)
        if url is None:
            # Без ссылки объявление бесполезно, а listings.url — NOT NULL.
            logger.warning("che168_unsafe_href href=%r source_id=%s", href, source_id)
            return None

        return UnifiedListing(
            source=self.source_name,
            source_id=source_id,
            url=url,
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
            # raw_payload для перепарсинга при смене селекторов (ТЗ п. 6.1).
            # html обрезан — флаг сообщает потребителю.
            raw={
                "title": title,
                "price_text": price_text,
                "html": card.html[:2000],
                "html_truncated": len(card.html) > 2000,
            },
        )

    @staticmethod
    def _extract_price(price_text: str | None, text: str) -> Decimal | None:
        """Извлекает цену: сначала из `price_node`, затем из текста (только «X万»).

        Fallback по тексту **требует** «万», иначе год выпуска («2020款»)
        матчится как цена `2020` (баг в старой версии).

        Args:
            price_text: Текст `price_node` или `None`.
            text: Полный текст карточки.

        Returns:
            Цена как `Decimal` или `None`.
        """
        if price_text:
            price = parse_wan_number(price_text, _PRICE_WAN_RE)
            if price is not None:
                return price
            # «238000» без 万 (маловероятно, но безопасно)
            return parse_wan_number(price_text, _PRICE_PLAIN_RE)
        return parse_wan_number(text, _PRICE_WAN_RE)

    @staticmethod
    def _extract_mileage(text: str) -> int | None:
        """Извлекает пробег «5.2万公里» → 52000 км.

        Args:
            text: Полный текст карточки.

        Returns:
            Пробег в км или `None`.
        """
        match = _MILEAGE_RE.search(text)
        if not match:
            return None
        try:
            value = Decimal(match.group(1))
        except InvalidOperation:
            return None
        if match.group(2) == "万":
            value *= 10_000
        return int(value)

    def _extract_source_id(self, card, link, href: str) -> str | None:
        """Достаёт `source_id` из `data-id` или URL.

        `data-id` из HTML валидируется как числовая строка: иначе
        `str(dict)` / `str(list)` создал бы мусорный ID с коллизиями.

        Args:
            card: Узел карточки.
            link: Узел ссылки или `None`.
            href: `href` ссылки.

        Returns:
            Строковый числовой ID или `None`.
        """
        data_id = card.attributes.get("data-id")
        if not data_id and link is not None:
            data_id = link.attributes.get("data-id")
        if isinstance(data_id, str) and data_id.strip().isdigit():
            return data_id.strip()
        match = _ID_RE.search(href or "")
        return match.group(1) if match else None

    def _extract_images(self, card) -> list[str]:
        """Собирает URL изображений карточки; фильтрует небезопасные схемы.

        Args:
            card: Узел карточки.

        Returns:
            Список абсолютных http(s)-URL.
        """
        urls: list[str] = []
        for img in card.css("img"):
            src = img.attributes.get("data-src") or img.attributes.get("src") or ""
            if src.startswith("data:"):  # пропускаем lazy-placeholder
                continue
            url = _safe_join(self._base_url + "/", src)
            if url:
                urls.append(url)
        return urls