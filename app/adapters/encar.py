"""EncarApiAdapter — источник encar.com (Корея, API).

Encar Search API возвращает JSON вида:
    {"Count": 1234, "SearchResults": [{ "Id": ..., "Manufacturer": ..., ...}]}

Важно: с осени 2026 API требует пагинационный параметр `sr`
("|<sort>|<offset>|<limit>"), `inav=|Metadata|Sort` и пустой `cursor`.
Без `sr` эндпоинт отвечает 200 с `{"Count": N}` и ПУСТЫМ SearchResults —
это и есть "сломанный контракт": счётчик приходит, объявлений нет.

Цены в API — в манах (1 ман = 10 000 KRW), поэтому приводим к KRW.
Конфигурируется через `sources.config` в БД (base_url, page_size, photo_prefix,
detail_url_template).
"""

from decimal import Decimal
from typing import Any

from app.adapters.base import BaseApiAdapter
from app.adapters.common.normalize import (
    normalize_fuel,
    normalize_make,
    normalize_transmission,
)
from app.schemas.criteria import SearchCriteria
from app.schemas.listing import FetchResult, UnifiedListing

DEFAULT_BASE_URL = "https://api.encar.com/search/car/list/general"
DEFAULT_PHOTO_PREFIX = "https://ci.encar.com"
DEFAULT_DETAIL_TEMPLATE = "https://fem.encar.com/car/detail/{source_id}"
DEFAULT_PAGE_SIZE = 20
DEFAULT_SORT = "ModifiedDate"
DEFAULT_USD_KRW = 1400  # запасной курс, если criteria.price в USD

# Сортировки, допустимые в sr (карта MobileModifiedDate→ModifiedDate и т.д.
# с фронта car.encar.com): ModifiedDate / PriceAsc / PriceDesc / MileageAsc /
# MileageDesc / Year (+ random для премиум-блока).

# Канонические EN-имена (normalize_make) → фильтр Manufacturer в API.
# Значения на сайте корейские («벤츠», «쉐보레(GM대우)»), латиницу API не матчит.
ENCAR_MAKE_NAMES: dict[str, str] = {
    "Hyundai": "현대",
    "Kia": "기아",
    "Genesis": "제네시스",
    "Chevrolet": "쉐보레",
    "BMW": "BMW",
    "Mercedes-Benz": "벤츠",
    "Audi": "아우디",
    "Volkswagen": "폭스바겐",
    "Toyota": "토요타",
    "Lexus": "렉서스",
    "Nissan": "닛산",
    "Honda": "혼다",
    "Ford": "포드",
    "Volvo": "볼보",
    "Porsche": "포르쉐",
    "Land Rover": "랜드로버",
    "Jaguar": "재규어",
    "MINI": "미니",
    "Renault": "르노",
    "Peugeot": "푸조",
    "Citroen": "시트로엥",
    "Fiat": "피아트",
    "Jeep": "지프",
    "Cadillac": "캐딜락",
    "Mazda": "마쯔다",
    "Mitsubishi": "미쯔비시",
    "Suzuki": "스즈키",
    "Subaru": "스바루",
}

# Популярные модели → ModelGroup в API (тоже корейский; латиница даёт 0).
ENCAR_MODEL_GROUPS: dict[str, str] = {
    "sonata": "쏘나타",
    "avante": "아반떼",
    "elantra": "아반떼",
    "grandeur": "그랜저",
    "azera": "그랜저",
    "santa fe": "싼타페",
    "tucson": "투싼",
    "palisade": "팰리세이드",
    "casper": "캐스퍼",
    "staria": "스타리아",
    "venue": "베뉴",
    "k5": "K5",
    "k3": "K3",
    "k7": "K7",
    "k8": "K8",
    "k9": "K9",
    "rio": "리오",
    "seltos": "셀토스",
    "sportage": "스포티지",
    "sorento": "쏘렌토",
    "carnival": "카니발",
    "ray": "레이",
    "morning": "모닝",
    "stonic": "스토닉",
    "soul": "소울",
    "niro": "니로",
    "ev6": "EV6",
    "g80": "G80",
    "g90": "G90",
    "gv70": "GV70",
    "gv80": "GV80",
    "x3": "X3",
    "x5": "X5",
    "x7": "X7",
    "3 series": "3시리즈",
    "5 series": "5시리즈",
    "7 series": "7시리즈",
    "e-class": "E클래스",
    "e class": "E클래스",
    "s-class": "S클래스",
    "s class": "S클래스",
    "c-class": "C클래스",
    "c class": "C클래스",
    "gle": "GLE",
    "camry": "캠리",
    "corolla": "코롤라",
    "highlander": "하이랜더",
    "sienna": "시엔나",
}

# unified-fuel → FuelType в API (корейские значения фасетов)
ENCAR_FUEL_NAMES: dict[str, str] = {
    "petrol": "가솔린",
    "diesel": "디젤",
    "hybrid": "가솔린+전기",
    "electric": "전기",
    "lpg": "LPG(일반인 구입_.)",  # значение фасета «LPG(일반인 구입)», скобка → _
}

# unified-transmission → Transmission в API
ENCAR_TRANSMISSION_NAMES: dict[str, str] = {
    "automatic": "오토",
    "manual": "수동",
    "cvt": "CVT",
}



class EncarApiAdapter(BaseApiAdapter):
    source_name = "encar"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._base_url = self.config.get("base_url", DEFAULT_BASE_URL)
        self._photo_prefix = self.config.get("photo_prefix", DEFAULT_PHOTO_PREFIX).rstrip("/")
        self._detail_template = self.config.get("detail_url_template", DEFAULT_DETAIL_TEMPLATE)
        self._page_size = int(self.config.get("page_size", DEFAULT_PAGE_SIZE))
        self._sort = self.config.get("sort", DEFAULT_SORT)
        self._usd_krw = float(self.config.get("usd_krw", DEFAULT_USD_KRW))

    # --- fetch ---------------------------------------------------------

    async def fetch(
        self, criteria: SearchCriteria, cursor: str | None = None
    ) -> FetchResult:
        page = int(cursor) if cursor else 0
        payload = await self.http.get_json(
            self._base_url, params=self._build_params(self._build_q(criteria), page)
        )
        raw_items = payload.get("SearchResults", [])
        items = [listing for item in raw_items if (listing := self._map_item(item))]

        total = int(payload.get("Count", len(items)))
        has_more = bool(items) and (page + 1) * self._page_size < total

        return FetchResult(
            items=items,
            next_cursor=str(page + 1) if has_more else None,
            has_more=has_more,
            source_meta={"total": total, "page": page},
        )

    async def healthcheck(self) -> bool:
        try:
            payload = await self.http.get_json(
                self._base_url, params=self._build_params("(And.Hidden.N.)", 0)
            )
            # 200 с одним Count и пустым SearchResults — контракт сломан,
            # считаем источник нездоровым.
            return bool(payload.get("SearchResults")) or bool(payload.get("Count"))
        except Exception:
            return False

    # --- helpers -------------------------------------------------------

    def _build_params(self, q: str, page: int) -> dict[str, Any]:
        """Параметры поиска.

        Контракт (окт 2026): объявления приходят только если передан `sr`
        ("|<sort>|<offset>|<limit>") + `inav=|Metadata|Sort` + пустой `cursor`.
        Без `sr` — только `{"Count": N}` без SearchResults.
        """
        return {
            "count": "true",
            "q": q,
            "sr": f"|{self._sort}|{page * self._page_size}|{self._page_size}",
            "inav": "|Metadata|Sort",
            "cursor": "",
        }

    def _build_q(self, c: SearchCriteria) -> str:
        """Формирование q-фильтра Encar (контракт окт 2026).

        Все условия — в ОДНОМ `(And …)`, разделитель условий `_.`:
            (And.Hidden.N._.Manufacturer.현대._.ModelGroup.쏘나타._.Price.range(1000..3000).)
        Старый синтаксис `(And.X.)(And Y.)` API принимает молча, но ИГНОРИРУЕТ
        (возвращает весь каталог), поэтому он больше не используется.

        Диапазоны: Price/Mileage — `range(a..b)` (цена в манах, 1 ман = 10 000
        KRW), Year — YYYYMM (`202201..202412`).
        Значения Manufacturer/ModelGroup/FuelType/Transmission — корейские
        фасеты; латиница матчится в 0, поэтому словари выше.
        """
        conditions = ["Hidden.N"]

        make = normalize_make(c.make) if c.make else None
        if make:
            kr_make = ENCAR_MAKE_NAMES.get(make)
            if kr_make:
                conditions.append(f"Manufacturer.{kr_make}")
        if c.model:
            kr_model = ENCAR_MODEL_GROUPS.get(c.model.strip().lower())
            if kr_model:
                conditions.append(f"ModelGroup.{kr_model}")

        if c.price_min is not None or c.price_max is not None:
            lo, hi = self._price_range_man(c)
            conditions.append(f"Price.range({lo}..{hi})")

        if c.year_from is not None or c.year_to is not None:
            y_lo = int(c.year_from) * 100 + 1 if c.year_from is not None else 190001
            y_hi = int(c.year_to) * 100 + 12 if c.year_to is not None else 209912
            conditions.append(f"Year.range({y_lo}..{y_hi})")

        if c.mileage_max is not None:
            conditions.append(f"Mileage.range(0..{int(c.mileage_max)})")

        fuel = normalize_fuel(c.fuel)
        if fuel and (kr_fuel := ENCAR_FUEL_NAMES.get(fuel)):
            conditions.append(f"FuelType.{kr_fuel}")

        transmission = normalize_transmission(c.transmission)
        if transmission and (kr_tr := ENCAR_TRANSMISSION_NAMES.get(transmission)):
            conditions.append(f"Transmission.{kr_tr}")

        return "(And." + "._.".join(conditions) + ".)"

    def _price_range_man(self, c: SearchCriteria) -> tuple[int, int]:
        """Цена критериев → диапазон в манах (1 ман = 10 000 KRW).

        `criteria.currency` учитывается: None/KRW — цена уже в вонах,
        USD — конвертация по `config.usd_krw` (без FX-сервиса в MVP).
        """
        currency = (c.currency or "KRW").upper()
        rate = self._usd_krw if currency == "USD" else 1
        lo = int(float(c.price_min) * rate / 10_000) if c.price_min is not None else 0
        hi = int(float(c.price_max) * rate / 10_000) if c.price_max is not None else 99_999_999
        return max(lo, 0), max(hi, 0)

    def _map_item(self, raw: dict[str, Any]) -> UnifiedListing | None:
        source_id = raw.get("Id")
        if source_id is None:
            return None

        price_man = raw.get("Price")
        price = Decimal(str(price_man)) * 10_000 if price_man is not None else None

        photos = [
            f"{self._photo_prefix}/{str(p).lstrip('/')}"
            for p in (raw.get("Photos") or ([raw["Photo"]] if raw.get("Photo") else []))
        ]

        # FormYear — строка года ('2022'), Year — YYYYMM (202208.0) в новом
        # контракте. Приоритет FormYear; из YYYYMM достаём год.
        year = self._extract_year(raw)

        # Subject в списке больше нет — собираем из марки и модели.
        title = (
            raw.get("Subject")
            or " ".join(
                p for p in (raw.get("Manufacturer"), raw.get("Model")) if p
            ).strip()
            or None
        )
        return UnifiedListing(
            source=self.source_name,
            source_id=str(source_id),
            url=self._detail_template.format(source_id=source_id),
            title=title,
            make=normalize_make(raw.get("Manufacturer")),
            model=raw.get("Model"),
            generation=raw.get("Badge") or raw.get("BadgeDetail"),
            year=year,
            price=price,
            currency="KRW",
            mileage_km=int(raw["Mileage"]) if raw.get("Mileage") else None,
            vin=raw.get("Vin") or None,
            body_type=raw.get("BodyType") or None,
            fuel=normalize_fuel(raw.get("FuelType")),
            transmission=normalize_transmission(raw.get("Transmission")),
            color=raw.get("Color") or None,
            location=raw.get("OfficeCityState") or raw.get("OfficeCity") or None,
            seller_type="dealer",
            images=photos,
            published_at=raw.get("PhotoDate"),
            raw=raw,
        )

    @staticmethod
    def _extract_year(raw: dict[str, Any]) -> int | None:
        """Год из FormYear ('2022') или Year (202208.0 → 2022)."""
        form_year = raw.get("FormYear")
        if form_year:
            try:
                return int(str(form_year)[:4])
            except ValueError:
                pass
        year = raw.get("Year")
        if year:
            year_num = int(float(year))
            return year_num // 100 if year_num > 190000 else year_num
        return None
