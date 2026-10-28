"""CarsComHtmlAdapter — источник cars.com (США, SSR-парсинг HTML).

Cars.com отдаёт список б/у авто на `/shopping/results/` в виде серверного
HTML (React SSR). Адаптер парсит его selectolax'ом и приводит к
UnifiedListing:

- цена «$24,998» → 24998 USD (исходная валюта, БЕЗ конвертации —
  конвертация на фронте, как KRW у encar); «Not Priced» → None;
- пробег «45,000 mi» → 72420 км (×1.60934);
- год — первое 4-значное число в заголовке («Used 2021 Toyota Camry SE»);
- марка/модель — из заголовка через MAKE_MAP (латиница, longest-match),
  модель — слова после марки до года/цифр (best effort, до 3 слов);
- локация — «City, ST» (двухбуквенный USPS-код штата валидируется по
  справочнику, иначе dealer-name матчился бы как город);
- `country="US"` прокидывается в UnifiedListing (GEO-V1 §1; дубль в
  `raw["country"]` — для перепарсинга старых raw_payload до бэкфилда);
- VIN в списке нет → дедуп по (source, source_id) + fuzzy по полям
  выполняется в общем конвейере (app/adapters/common/dedup.py).

ЖИВАЯ ПРОВЕРКА (2026-10-28, curl, 2 попытки: /shopping/results/ и
/shopping/results/?stock_type=used&page_size=50): HTTP 403 от анти-бота
Akamai, тело — Reference-страница ошибки. Реальная вёрстка недоступна
без headless-браузера/резидентного прокси US. Селекторы ниже —
структурные (классические классы cars.com + data-qa варианты), проверены
только на фикстурах тестов.

TODO(включение): сверить селекторы с живым HTML (browser-use прогон или
прокси US), при необходимости поправить `sources.config` в БД — код
адаптера менять не нужно (ТЗ п. 6.1: код в Git, конфиг в БД).

Пагинация: параметр `page` (https://www.cars.com/shopping/results/
?stock_type=used&page=2&page_size=20). `fetch(criteria, cursor)` **не
применяет `criteria`** — фильтрация выполняется downstream
(pipeline/FilterService), как у che168. Пустая страница N>1 трактуется
как конец выдачи (has_more=False) с warning.
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

DEFAULT_BASE_URL = "https://www.cars.com"
DEFAULT_LIST_PATH = "/shopping/results/"
# Полный набор query-параметров собирается адаптером: httpx `params`
# ЗАМЕНЯЕТ существующий query-string URL целиком (проверено), поэтому
# базовые фильтры (б/у) живут здесь, а не в list_path.
DEFAULT_PARAMS: dict[str, str] = {"stock_type": "used", "page_size": "20"}
DEFAULT_MAX_PAGES = 5

# Селекторы — константы класса (как che168), переопределяются `sources.config`.
# СВЕРИТЬ С ЖИВЫМ HTML ПЕРЕД ВКЛЮЧЕНИЕМ: живой список отдаёт 403 (Akamai,
# см. докстринг модуля), значения проверены только на фикстурах тестов.
# Два поколения вёрстки: классические классы (vehicle-card) и data-qa.
DEFAULT_CARD_SELECTOR = 'div.vehicle-card, div[data-qa="vehicle-card"]'
DEFAULT_TITLE_SELECTOR = 'a.vehicle-card-link, .title a, a[href*="/vehicledetail/"]'
DEFAULT_PRICE_SELECTOR = 'span.primary-price, [data-qa="primary-price"]'
DEFAULT_MILEAGE_SELECTOR = 'div.mileage, .miles, [data-qa="mileage"]'
DEFAULT_LINK_SELECTOR = 'a[href*="/vehicledetail/"], a[href]'
DEFAULT_LOCATION_SELECTOR = '.dealer-location, .vehicle-dealer, [data-qa="dealer-location"]'

# mappingproxy: никто случайно не мутирует общий HEADERS в процессе.
HEADERS = MappingProxyType(
    {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        # Анти-боты ожидают Referer на свой же сайт.
        "Referer": DEFAULT_BASE_URL + "/",
    }
)

# Мили → км (тот же коэффициент, что у dubizzle). 45,000 mi → 72 420 км.
MILES_TO_KM = Decimal("1.60934")

# ID из ссылки /vehicledetail/detail/<id>/: числовой (классика) или
# UUID (редизайн) — поддерживаем оба.
_ID_RE = re.compile(r"/vehicledetail/detail/([0-9a-fA-F-]{6,})")
# Цена: «$24,998» / «24,998» / «Not Priced» (не матчится → None).
_PRICE_RE = re.compile(r"\$?\s*(\d[\d,]*(?:\.\d+)?)")
# Пробег: «45,000 mi» / «45,000 mi.» / «45,000 miles».
_MILEAGE_RE = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(mi|miles?)\b\.?", re.I)
# Год: 4-значное число в заголовке/тексте (1900–2099), первое вхождение.
_YEAR_RE = re.compile(r"\b(19\d{2}|20\d{2})\b")
# Локация «City, ST»: ST — валидный USPS-код (отсекает «Motors, Co»).
# Группа 1 — город (без запятой и штата), группа 2 — штат.
_LOCATION_RE = re.compile(r"([A-Za-z][A-Za-z .'\-]{1,30}?),\s*([A-Z]{2})\b")

# USPS-коды штатов/территорий (50 + DC + PR + др.) для валидации локации.
US_STATE_CODES: frozenset[str] = frozenset(
    {
        "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI",
        "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI",
        "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC",
        "ND", "OH", "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT",
        "VT", "VA", "WA", "WV", "WI", "WY", "DC", "PR", "GU", "VI", "AA",
        "AE", "AP",
    }
)

# Разрешённые хосты изображений: cars.com раздаёт фото с внешних CDN
# дилеров — базовая проверка «только cars.com» срезала бы всю галерею.
# Расширяется через `sources.config.image_hosts` без деплоя.
DEFAULT_IMAGE_HOSTS: tuple[str, ...] = (
    "cars.com",
    "cdn.cars.com",
    "pictures.dealer.com",
    "cdn.dealereprocess.com",
    "cdn.carfax.com",
)

# Схемы, запрещённые в href/src из чужого HTML.
_FORBIDDEN_SCHEMES = frozenset({"javascript", "data", "vbscript", "file"})

# Ключи MAKE_MAP — латиница (в заголовках cars.com марка латиницей),
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

# Слова, НЕ попадающие в модель при разборе заголовка (шум листинга US).
# Внимание: «Sport» НЕ стоп-слово — «Range Rover Sport» это имя модели.
_MODEL_STOPWORDS = frozenset(
    {
        "used", "new", "certified", "sale", "price", "reduced", "great",
        "deal", "special", "offer", "stock", "mileage", "one", "owner",
        "clean", "automatic", "manual", "awd", "fwd", "rwd", "4wd", "drive",
        "package", "edition", "coupe", "sedan", "suv", "hatchback", "wagon",
        "convertible", "cab", "crew", "extended", "touring",
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
        Безопасный абсолютный http(s)-URL домена cars.com или `None`.
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
    # Только свой домен (и его поддомены, например cdn.carfax.com):
    # `//evil.com` иначе даёт внешний URL, который уходит в БД и в карточку.
    base_host = (urlparse(base).hostname or "").lower()
    host = (parsed.hostname or "").lower()
    root = ".".join(base_host.split(".")[-2:]) if base_host else ""
    if not root or not (host == root or host.endswith("." + root)):
        return None
    return joined


def _safe_join_media(base: str, href: str | None, allowed_hosts: tuple[str, ...]) -> str | None:
    """Как `_safe_join`, но домен проверяется по allowlist (CDN изображений).

    Схема обязательна http(s), запрещённые схемы (`javascript:` и т.п.)
    и postmapping-мусор (`//evil.com/x` вне allowlist) отсекаются.

    Args:
        base: Базовый URL (с trailing slash) для относительных ссылок.
        href: Относительный или абсолютный URL из HTML.
        allowed_hosts: Кортеж разрешённых доменных корней (`"cars.com"`).

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
    host = (parsed.hostname or "").lower()
    for root in allowed_hosts:
        root = root.lower()
        if host == root or host.endswith("." + root):
            return joined
    return None


def extract_make_en(text: str | None) -> tuple[str | None, str | None]:
    """Марка + модель из заголовка: «Used 2021 Toyota Camry SE» → (Toyota, Camry SE).

    Марка — longest-match по справочнику (латиница, word boundary), модель —
    слова после марки до года/цифры/стоп-слова, максимум 3 слова
    (Land Cruiser / Range Rover — двухсловные модели; трим «SE» может
    попасть в модель — задокументированный best effort без справочника
    моделей). Год в начале заголовка (формат cars.com) игнорируется.

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
    """«$24,998» → 24998; «Not Priced» → None.

    Args:
        raw: Текст ценового узла или `None`.

    Returns:
        Цена (USD, без конвертации) как `Decimal` или `None`.
    """
    if not raw:
        return None
    match = _PRICE_RE.search(raw)
    if match is None:
        return None
    try:
        return Decimal(match.group(1).replace(",", ""))
    except InvalidOperation:
        return None


def parse_mileage(raw: str | None) -> int | None:
    """«45,000 mi» → 72420 км (×1.60934, округление до целых км).

    В отличие от dubizzle, km-вариант не ищется: cars.com показывает
    пробег только в милях.

    Args:
        raw: Текст узла пробега или всего текст карточки.

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
    return int((value * MILES_TO_KM).quantize(Decimal("1")))


def parse_location(raw: str | None) -> str | None:
    """«Scottsdale, AZ 85254» → «Scottsdale, AZ»; ST валидируется по USPS.

    Args:
        raw: Текст узла локации или полный текст карточки.

    Returns:
        Строка «City, ST» или `None`.
    """
    if not raw:
        return None
    for match in _LOCATION_RE.finditer(raw):
        city, state = match.group(1), match.group(2)
        if state in US_STATE_CODES:
            return f"{city.strip()}, {state}"
    return None


class CarsComHtmlAdapter(BaseApiAdapter):
    """SSR-парсер cars.com (kind='parser' в таблице `sources`).

    Особенности:
    - `fetch` не применяет `criteria` — фильтрация downstream;
    - `_max_pages` ограничивает пагинацию `page=` (клампится к >= 1);
    - URL и картинки из HTML валидируются по схеме и домену;
    - селекторы НЕ сверены с живым HTML (Akamai 403) — источник включать
      только после прогона healthcheck с прокси US (см. докстринг модуля).

    Attributes:
        source_name: `"cars_com"`.
    """

    source_name = "cars_com"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._base_url = self.config.get("base_url", DEFAULT_BASE_URL).rstrip("/")
        self._list_path = self.config.get("list_path", DEFAULT_LIST_PATH)
        # params из БД (например page_size) мержатся с дефолтами.
        params = dict(DEFAULT_PARAMS)
        extra_params = self.config.get("params")
        if isinstance(extra_params, dict):
            params.update({k: str(v) for k, v in extra_params.items()})
        self._params = params
        # Разрешённые хосты изображений (CDN дилеров), см. DEFAULT_IMAGE_HOSTS.
        hosts = self.config.get("image_hosts")
        self._image_hosts = (
            tuple(hosts) if isinstance(hosts, (list, tuple)) else DEFAULT_IMAGE_HOSTS
        )
        self._card_selector = self.config.get("card_selector", DEFAULT_CARD_SELECTOR)
        self._title_selector = self.config.get("title_selector", DEFAULT_TITLE_SELECTOR)
        self._price_selector = self.config.get("price_selector", DEFAULT_PRICE_SELECTOR)
        self._mileage_selector = self.config.get(
            "mileage_selector", DEFAULT_MILEAGE_SELECTOR
        )
        self._link_selector = self.config.get("link_selector", DEFAULT_LINK_SELECTOR)
        self._location_selector = self.config.get(
            "location_selector", DEFAULT_LOCATION_SELECTOR
        )
        try:
            self._max_pages = max(1, int(self.config.get("max_pages", DEFAULT_MAX_PAGES)))
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"cars_com: invalid max_pages={self.config.get('max_pages')!r}"
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
        """Забирает страницу списка cars.com.

        `criteria` не применяется: cars.com-список не фильтруется клиентски;
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
        params = {**self._params, "page": str(page)}
        resp = await self.http.get(
            self._base_url + self._list_path, params=params, headers=dict(HEADERS)
        )
        items = self._parse(resp.text)

        if page > 1 and not items:
            # Конец выдачи либо вёрстка/анти-бот сломались — healthcheck решает.
            logger.warning(
                "cars_com_empty_page page=%d url=%s (pagination or layout may be broken)",
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
            raise FetchError(f"cars_com: invalid cursor {cursor!r}") from exc
        if page < 1:
            raise FetchError(f"cars_com: cursor must be >= 1, got {page}")
        return page

    async def healthcheck(self) -> bool:
        """200 + контейнер списка = источник жив (вёрстка не сломана).

        ВАЖНО: анти-бот-страница 403 отдаёт не-2xx — healthcheck вернёт
        `False` (прокси/селекторы не работают), пустая страница 200 без
        карточек — тоже `False`.

        Returns:
            `True`, если в ответе есть хотя бы одна карточка по текущему
            `card_selector`; `False` при ошибке HTTP или пустом списке.
        """
        try:
            resp = await self.http.get(
                self._base_url + self._list_path,
                params=dict(self._params),
                headers=dict(HEADERS),
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
                    "cars_com_card_parse_error source=%s href=%s",
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

        # Заголовок: узел ссылки-заголовка либо текст vehicledetail-ссылки.
        title_node = card.css_first(self._title_selector) or link
        title = title_node.text(strip=True) if title_node else None
        text = card.text(separator=" ", strip=True)

        price_node = card.css_first(self._price_selector)
        price_text = price_node.text(strip=True) if price_node else None
        price = parse_price(price_text)

        mileage_node = card.css_first(self._mileage_selector)
        mileage_text = mileage_node.text(strip=True) if mileage_node else None
        mileage_km = parse_mileage(mileage_text)
        if mileage_km is None:  # узла нет — ищем по всему тексту карточки
            mileage_km = parse_mileage(text)

        year = self._extract_year(title, text)
        make, model = extract_make_en(title)
        location = self._extract_location(card, text)

        # URL и картинки из чужого HTML — валидируем схему и домен.
        url = _safe_join(self._base_url + "/", href)
        if url is None:
            # Без ссылки объявление бесполезно, а listings.url — NOT NULL.
            logger.warning("cars_com_unsafe_href href=%r source_id=%s", href, source_id)
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
            currency="USD",
            mileage_km=mileage_km,
            vin=None,  # VIN в списке cars.com не публикуется
            fuel=detect_fuel(text) if self._fuel_from_text else None,
            transmission=(
                detect_transmission(text) if self._transmission_from_text else None
            ),
            location=location,
            seller_type="dealer",  # листинг cars.com — дилерские объявления
            images=self._extract_images(card),
            # Страна в UnifiedListing (GEO-V1 §1); дубль в raw — для
            # перепарсинга старых raw_payload до бэкфилда.
            country="US",
            raw={
                "country": "US",
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
        """Год из заголовка («Used 2021 Toyota Camry SE»), затем из текста.

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
        """«City, ST» из узла локации; fallback — поиск по всему тексту.

        Args:
            card: Узел карточки.
            text: Полный текст карточки.

        Returns:
            Строка «City, ST» или `None`.
        """
        node = card.css_first(self._location_selector)
        node_text = node.text(separator=" ", strip=True) if node else None
        return parse_location(node_text) or parse_location(text)

    @staticmethod
    def _extract_source_id(card, href: str) -> str | None:
        """Достаёт `source_id` из `data-listing-id` или ссылки карточки.

        Ссылка вида `/vehicledetail/detail/1234567890/` → `"1234567890"`
        (число) или `/vehicledetail/detail/8f2a1b3c-…/` → UUID. `data-id`
        из HTML валидируется как непустая строка без пробелов.

        Args:
            card: Узел карточки.
            href: `href` ссылки.

        Returns:
            ID строки или `None`.
        """
        data_id = card.attributes.get("data-listing-id")
        if isinstance(data_id, str) and data_id.strip() and " " not in data_id:
            return data_id.strip()
        match = _ID_RE.search(href or "")
        return match.group(1) if match else None

    def _extract_images(self, card) -> list[str]:
        """Собирает URL изображений карточки; фильтрует небезопасные схемы.

        В отличие от ссылок, изображения разрешены с любого хоста из
        `image_hosts` (CDN дилеров cars.com), а не только cars.com.

        Args:
            card: Узел карточки.

        Returns:
            Список абсолютных http(s)-URL из allowlist-хостов.
        """
        urls: list[str] = []
        for img in card.css("img"):
            src = (
                img.attributes.get("data-src")
                or img.attributes.get("data-lazy-load")
                or img.attributes.get("src")
                or ""
            )
            if src.startswith("data:"):  # пропускаем lazy-placeholder
                continue
            url = _safe_join_media(self._base_url + "/", src, self._image_hosts)
            if url:
                urls.append(url)
        return urls
