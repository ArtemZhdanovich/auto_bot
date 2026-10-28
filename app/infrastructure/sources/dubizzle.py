"""DubizzleHtmlAdapter — источник dubizzle.com (ОАЭ, SSR-парсинг HTML).

Dubizzle (группа OLX) отдаёт список б/у авто на `/motors/used-cars/` в виде
серверного HTML. Адаптер парсит его selectolax'ом и приводит к UnifiedListing:

- цена «AED 125,000» → 125000 AED (исходная валюта, БЕЗ конвертации —
  конвертация на фронте, как KRW у encar);
- пробег «45,000 mi» → 72420 км (×1.60934); «123,456 km» → 123456 км;
- год — первое 4-значное число в заголовке («Toyota Land Cruiser 2021»);
- марка/модель — из заголовка через MAKE_MAP (латиница, longest-match),
  модель — слова после марки до года/цифр (best effort, до 3 слов);
- город — узел локации карточки, fallback — поиск известного города ОАЭ
  в тексте карточки (Dubai/Abu Dhabi/Sharjah/Ajman/Al Ain/…);
- `country="AE"` прокидывается в UnifiedListing (GEO-V1 §1; дубль в
  `raw["country"]` — для перепарсинга старых raw_payload до бэкфилда);
- VIN в списке нет → дедуп по (source, source_id) + fuzzy по полям
  выполняется в общем конвейере (app/adapters/common/dedup.py).

ЖИВАЯ ПРОВЕРКА (2026-10-28, curl, 2 попытки): dubizzle отдаёт HTTP 200
на список, но тело — Imperva-челлендж «Pardon Our Interruption»
(window.reese…, JS-запуск обязателен), реальная вёрстка недоступна без
headless-браузера/резидентного прокси. Селекторы ниже — структурные
(data-aut-id — вёрстка OLX-поколения, data-testid — редизайн 2023+),
проверены только на фикстурах тестов.

TODO(включение): сверить селекторы с живым HTML (browser-use прогон или
прокси AE), при необходимости поправить `sources.config` в БД — код
адаптера менять не нужно (ТЗ п. 6.1: код в Git, конфиг в БД).

Пагинация: `?page=N` (стандарт OLX). `fetch(criteria, cursor)` **не
применяет `criteria`** — фильтрация выполняется downstream
(pipeline/FilterService), как у che168. Пустая страница N>1 трактуется
как конец выдачи (has_more=False) с warning — Imperva/смена вёрстки
детектируется healthcheck'ом.
"""

import logging
import re
from datetime import datetime
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

DEFAULT_BASE_URL = "https://www.dubizzle.com"
DEFAULT_LIST_PATH = "/motors/used-cars/"
DEFAULT_MAX_PAGES = 5

# Селекторы — константы класса (как che168), переопределяются `sources.config`.
# СВЕРИТЬ С ЖИВЫМ HTML ПЕРЕД ВКЛЮЧЕНИЕМ: живой список закрыт Imperva-челленджем
# (см. докстринг модуля), значения проверены только на фикстурах тестов.
# Два поколения OLX-вёрстки: data-aut-id (старая) и data-testid (редизайн).
DEFAULT_CARD_SELECTOR = 'li[data-aut-id="itemCard"], li[data-testid="listing-card"]'
DEFAULT_TITLE_SELECTOR = '[data-aut-id="itemTitle"], h2, h3'
DEFAULT_PRICE_SELECTOR = '[data-aut-id="itemPrice"], [data-testid="ad-price"]'
DEFAULT_LINK_SELECTOR = 'a[href*="/motors/"], a[href]'
DEFAULT_LOCATION_SELECTOR = (
    '[data-aut-id="item-location"], [data-testid="ad-item-location"], '
    '[data-testid="ad-location"]'
)

# mappingproxy: никто случайно не мутирует общий HEADERS в процессе.
HEADERS = MappingProxyType(
    {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,ar;q=0.8",
        # Анти-боты ожидают Referer на свой же сайт.
        "Referer": DEFAULT_BASE_URL + "/",
    }
)

# Мили → км (стнд. переводной коэффициент). 45,000 mi → 72 420 км.
MILES_TO_KM = Decimal("1.60934")

# ID объявления из ссылки вида
# /motors/used-cars/toyota/land-cruiser-2021-6732541/ → 6732541.
# Берём ПОСЛЕДНЕЕ число из 5+ цифр после дефиса (дефис-год «-2021-» не
# матчится: требуется >= 5 цифр).
_ID_RE = re.compile(r"-(\d{5,})(?:[/?#]|$)")
# Цена: «AED 125,000» / «AED 125,000 negotiable» (нормализуется ниже).
_PRICE_RE = re.compile(r"(?:AED|aed)\s*(\d[\d,]*(?:\.\d+)?)")
# Пробег: «45,000 mi» / «45,000 miles» / «123,456 km» / «123,456 kms».
_MILEAGE_RE = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(kms?|kilometers?|mi|miles?)\b", re.I)
# Год: 4-значное число в заголовке/тексте (1900–2099), первое вхождение.
_YEAR_RE = re.compile(r"\b(19\d{2}|20\d{2})\b")
# Города ОАЭ для fallback-детекции города в тексте карточки.
UAE_CITIES: tuple[str, ...] = (
    "Dubai", "Abu Dhabi", "Sharjah", "Ajman", "Al Ain", "Ras Al Khaimah",
    "Umm Al Quwain", "Fujairah", "Dibba",
)

# Схемы, запрещённые в href/src из чужого HTML.
_FORBIDDEN_SCHEMES = frozenset({"javascript", "data", "vbscript", "file"})

# Ключи MAKE_MAP — латиница (в заголовках dubizzle марка латиницей),
# длинные раньше: «Land Rover» проверяется до коротких ключей.
# Канонические значения добавлены (в lower — матчим по lowercased-тексту),
# чтобы «Mercedes-Benz» / «Land Rover» в заголовке матчились даже без
# отдельного ключа.
_TITLE_MAKES: list[tuple[str, str]] = sorted(
    {
        **{k.lower(): v for k, v in MAKE_MAP.items()},
        **{v.lower(): v for v in set(MAKE_MAP.values())},
    }.items(),
    key=lambda kv: len(kv[0]),
    reverse=True,
)

# Слова, НЕ попадающие в модель при разборе заголовка (шум объявлений).
_MODEL_STOPWORDS = frozenset(
    {
        "used", "new", "gcc", "spec", "specs", "offer", "urgent", "sale",
        "automatic", "manual", "mileage", "export", "american", "japanese",
        "grade", "edition", "package", "sunroof", "navi", "clean", "low",
        "mile", "only", "one", "owner", "full", "service", "history", "ready",
        "stock", "driven", "well", "maintained", "bank", "loan", "cash",
    }
)


def _safe_join(base: str, href: str | None) -> str | None:
    """`urljoin` с валидацией схемы и финального URL (как che168).

    `urljoin("https://a/", "javascript:alert(1)")` возвращает
    `javascript:alert(1)` — XSS-вектор при рендере `<a href>`.
    `//evil.com/x` → `https://evil.com/x` — трекинг-пиксель. Отсекаем.

    Args:
        base: Базовый URL (с trailing slash).
        href: Относительный или абсолютный URL из HTML.

    Returns:
        Безопасный абсолютный http(s)-URL домена dubizzle или `None`.
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
    # Только свой домен (и его поддомены, например images.dubizzle.com):
    # `//evil.com` иначе даёт внешний URL, который уходит в БД и в карточку.
    base_host = (urlparse(base).hostname or "").lower()
    host = (parsed.hostname or "").lower()
    root = ".".join(base_host.split(".")[-2:]) if base_host else ""
    if not root or not (host == root or host.endswith("." + root)):
        return None
    return joined


def extract_make_en(text: str | None) -> tuple[str | None, str | None]:
    """Марка + модель из заголовка: «Toyota Land Cruiser 2021» → (Toyota, Land Cruiser).

    Марка — longest-match по справочнику (латиница, word boundary), модель —
    слова после марки до первого года/цифры/стоп-слова, максимум 3 слова
    (Land Cruiser / Range Rover — двухсловные модели). Best effort: без
    справочника моделей мусор из заголовка не извлекается.

    Args:
        text: Заголовок объявления.

    Returns:
        Кортеж `(make, model)`; компоненты `None`, если не найдены.
    """
    if not text:
        return None, None
    lowered = text.lower()
    for key, make in _TITLE_MAKES:
        match = re.search(rf"\b{re.escape(key)}\b", lowered)
        if match is None:
            continue
        # Токены модели — из исходного текста (регистр «GLE»/«SE» важен);
        # индексы lowered и text совпадают (lower() не меняет длину).
        model_words: list[str] = []
        for token in text[match.end():].split():
            if len(model_words) >= 3 or not token.isalpha():
                break  # год («2021»), объём («5.7L») или лимит слов
            if token.lower() in _MODEL_STOPWORDS or token.lower() in ("mi", "km"):
                break
            model_words.append(token)
        model = " ".join(model_words) or None
        return make, model
    return None, None


def parse_price(raw: str | None) -> Decimal | None:
    """«AED 125,000» → 125000; None, если цены нет.

    Args:
        raw: Текст ценового узла или `None`.

    Returns:
        Цена (AED, без конвертации) как `Decimal` или `None`.
    """
    if not raw:
        return None
    match = _PRICE_RE.search(raw)
    digits = match.group(1) if match else raw
    try:
        return Decimal(digits.replace(",", ""))
    except InvalidOperation:
        return None


def parse_mileage(raw: str | None) -> int | None:
    """«45,000 mi» → 72420 км; «123,456 km» → 123456 км; None, если нет.

    Первое совпадение в тексте. Мили переводятся с округлением до целых км
    (45 000 × 1.60934 = 72 420,3 → 72 420).

    Args:
        raw: Текст карточки (или отдельного узла пробега).

    Returns:
        Пробег в км (int) или `None`.
    """
    if not raw:
        return None
    match = _MILEAGE_RE.search(raw)
    if not match:
        return None
    try:
        value = Decimal(match.group(1).replace(",", ""))
    except InvalidOperation:
        return None
    unit = match.group(2).lower()
    if unit in ("mi", "miles", "mile"):
        value *= MILES_TO_KM
    return int(value.quantize(Decimal("1")))


class DubizzleHtmlAdapter(BaseApiAdapter):
    """SSR-парсер dubizzle.com (kind='parser' в таблице `sources`).

    Особенности:
    - `fetch` не применяет `criteria` — фильтрация downstream;
    - `_max_pages` ограничивает пагинацию `?page=N` (клампится к >= 1);
    - URL и картинки из HTML валидируются по схеме и домену;
    - селекторы НЕ сверены с живым HTML (Imperva) — источник включать
      только после прогона healthcheck с прокси AE (см. докстринг модуля).

    Attributes:
        source_name: `"dubizzle"`.
    """

    source_name = "dubizzle"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._base_url = self.config.get("base_url", DEFAULT_BASE_URL).rstrip("/")
        self._list_path = self.config.get("list_path", DEFAULT_LIST_PATH)
        self._card_selector = self.config.get("card_selector", DEFAULT_CARD_SELECTOR)
        self._title_selector = self.config.get("title_selector", DEFAULT_TITLE_SELECTOR)
        self._price_selector = self.config.get("price_selector", DEFAULT_PRICE_SELECTOR)
        self._link_selector = self.config.get("link_selector", DEFAULT_LINK_SELECTOR)
        self._location_selector = self.config.get(
            "location_selector", DEFAULT_LOCATION_SELECTOR
        )
        try:
            self._max_pages = max(1, int(self.config.get("max_pages", DEFAULT_MAX_PAGES)))
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"dubizzle: invalid max_pages={self.config.get('max_pages')!r}"
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
        """Забирает страницу списка dubizzle.

        `criteria` не применяется: dubizzle-список не фильтруется клиентски;
        фильтрация — downstream. См. докстринг модуля.

        Args:
            criteria: Критерии поиска (игнорируются).
            cursor: Номер страницы строкой (`"1"`, `"2"`, …) или `None`.

        Returns:
            `FetchResult` с объявлениями и `next_cursor` (или `None`).

        Raises:
            FetchError: При невалидном `cursor` или ошибке HTTP.
        """
        page = self._parse_cursor(cursor)
        params = {"page": str(page)}
        resp = await self.http.get(
            self._base_url + self._list_path, params=params, headers=dict(HEADERS)
        )
        items = self._parse(resp.text)

        if page > 1 and not items:
            # Конец выдачи либо вёрстка/Imperva сломались — healthcheck решает.
            logger.warning(
                "dubizzle_empty_page page=%d url=%s (pagination or layout may be broken)",
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
            raise FetchError(f"dubizzle: invalid cursor {cursor!r}") from exc
        if page < 1:
            raise FetchError(f"dubizzle: cursor must be >= 1, got {page}")
        return page

    async def healthcheck(self) -> bool:
        """200 + контейнер списка = источник жив (вёрстка не сломана).

        ВАЖНО: Imperva-челлендж тоже отдаёт 200, но без карточек — тогда
        healthcheck честно возвращает `False` (селекторы/прокси не работают).

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
                    "dubizzle_card_parse_error source=%s href=%s",
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
            `UnifiedListing` или `None`, если не удалось извлечь `source_id`/url.
        """
        link = card.css_first(self._link_selector)
        href = (link.attributes.get("href") if link else None) or ""
        source_id = self._extract_source_id(card, href)
        if source_id is None:
            return None

        title_node = card.css_first(self._title_selector)
        title = title_node.text(strip=True) if title_node else None
        text = card.text(separator=" ", strip=True)

        price_node = card.css_first(self._price_selector)
        price_text = price_node.text(strip=True) if price_node else None
        price = parse_price(price_text)

        mileage_km = parse_mileage(text)

        year = self._extract_year(title, text)
        make, model = extract_make_en(title)
        location = self._extract_location(card, text)

        # URL и картинки из чужого HTML — валидируем схему и домен.
        url = _safe_join(self._base_url + "/", href)
        if url is None:
            # Без ссылки объявление бесполезно, а listings.url — NOT NULL.
            logger.warning("dubizzle_unsafe_href href=%r source_id=%s", href, source_id)
            return None

        return UnifiedListing(
            source=self.source_name,
            source_id=source_id,
            url=url,
            title=title,
            make=make,
            model=model,
            year=year,
            price=price,
            currency="AED",
            mileage_km=mileage_km,
            vin=None,  # VIN в списке dubizzle не публикуется
            fuel=detect_fuel(text) if self._fuel_from_text else None,
            transmission=(
                detect_transmission(text) if self._transmission_from_text else None
            ),
            location=location,
            seller_type="dealer",  # большинство моторс-листов dubizzle — дилеры
            images=self._extract_images(card),
            # Страна в UnifiedListing (GEO-V1 §1); дубль в raw — для
            # перепарсинга старых raw_payload до бэкфилда.
            country="AE",
            raw={
                "country": "AE",
                "title": title,
                "price_text": price_text,
                "location": location,
                # html обрезан — флаг сообщает потребителю.
                "html": card.html[:2000],
                "html_truncated": len(card.html) > 2000,
            },
        )

    @staticmethod
    def _extract_year(title: str | None, text: str) -> int | None:
        """Год из заголовка («…2021»), затем из текста карточки.

        Диапазон 1980..(текущий+1) отсекает «3000» и опечатки.

        Args:
            title: Заголовок объявления или `None`.
            text: Полный текст карточки.

        Returns:
            Год (int) или `None`.
        """
        for source in (title, text):
            if not source:
                continue
            match = _YEAR_RE.search(source)
            if match:
                year = int(match.group(1))
                if 1980 <= year <= datetime.now().year + 1:
                    return year
        return None


    def _extract_location(self, card, text: str) -> str | None:
        """Город из узла локации; fallback — детекция города ОАЭ в тексте.

        Порядок: узел локации → полный текст карточки; если город не найден
        нигде, но узел непуст — возвращается его текст как есть (нестандартный
        формат лучше, чем ничего).

        Args:
            card: Узел карточки.
            text: Полный текст карточки.

        Returns:
            Название города (`"Dubai"`, `"Abu Dhabi"`, …) или `None`.
        """
        node = card.css_first(self._location_selector)
        node_text = (
            node.text(separator=" ", strip=True) if node is not None else None
        )
        for source_text in (node_text, text):
            if not source_text:
                continue
            lowered = source_text.lower()
            for city in UAE_CITIES:
                if city.lower() in lowered:
                    return city
        return node_text or None

    @staticmethod
    def _extract_source_id(card, href: str) -> str | None:
        """Достаёт `source_id` из `data-id` или хвоста ссылки.

        Ссылка вида `/motors/used-cars/toyota/land-cruiser-2021-6732541/`
        → `"6732541"` (последняя дефис-группа из 5+ цифр; год «2021» из
        4 цифр не матчится). `data-id` из HTML валидируется как числовая
        строка — иначе мусор создал бы коллизии ID.

        Args:
            card: Узел карточки.
            href: `href` ссылки.

        Returns:
            Строковый числовой ID или `None`.
        """
        data_id = card.attributes.get("data-id")
        if isinstance(data_id, str) and data_id.strip().isdigit():
            return data_id.strip()
        matches = _ID_RE.findall(href or "")
        return matches[-1] if matches else None

    def _extract_images(self, card) -> list[str]:
        """Собирает URL изображений карточки; фильтрует небезопасные схемы.

        Args:
            card: Узел карточки.

        Returns:
            Список абсолютных http(s)-URL домена dubizzle.
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
