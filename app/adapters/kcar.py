"""KCarApiAdapter — источник kcar.com (Корея, API) — этап 7, второй источник.

Search API kcar.com возвращает JSON вида:
    {"totalCnt": 1234, "list": [{"carSeq": ..., "carName": ..., ...}]}

Цены в API — в манах (1 ман = 10 000 KRW), единица настраивается
`config.price_unit` (ТЗ п. 6.1: конфиг в БД, код в Git). Маппинг полей
задан по публичному поисковому API; при изменении источника правится
конфиг (`base_url`, `detail_url_template`) без деплоя.
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

DEFAULT_BASE_URL = "https://api.kcar.com/dst/api/car/search/list"
DEFAULT_PHOTO_PREFIX = "https://ci.kcar.com"
DEFAULT_DETAIL_TEMPLATE = "https://www.kcar.com/rebuilt/car/info/carView.do?carSeq={source_id}"
DEFAULT_PAGE_SIZE = 20


class KCarApiAdapter(BaseApiAdapter):
    source_name = "kcar"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._base_url = self.config.get("base_url", DEFAULT_BASE_URL)
        self._photo_prefix = self.config.get("photo_prefix", DEFAULT_PHOTO_PREFIX).rstrip("/")
        self._detail_template = self.config.get("detail_url_template", DEFAULT_DETAIL_TEMPLATE)
        self._page_size = int(self.config.get("page_size", DEFAULT_PAGE_SIZE))
        self._price_unit = int(self.config.get("price_unit", 10_000))

    # --- fetch ---------------------------------------------------------

    async def fetch(
        self, criteria: SearchCriteria, cursor: str | None = None
    ) -> FetchResult:
        page = int(cursor) if cursor else 1  # у KCar страницы с 1
        payload = await self.http.get_json(
            self._base_url,
            params={"page": str(page), "count": str(self._page_size), **self._build_params(criteria)},
        )
        raw_items = payload.get("list", [])
        items = [listing for item in raw_items if (listing := self._map_item(item))]

        total = int(payload.get("totalCnt", len(items)))
        has_more = bool(items) and page * self._page_size < total

        return FetchResult(
            items=items,
            next_cursor=str(page + 1) if has_more else None,
            has_more=has_more,
            source_meta={"total": total, "page": page},
        )

    async def healthcheck(self) -> bool:
        try:
            await self.http.get_json(
                self._base_url, params={"page": "1", "count": "1"}
            )
            return True
        except Exception:
            return False

    # --- helpers -------------------------------------------------------

    def _build_params(self, c: SearchCriteria) -> dict[str, str]:
        """Параметры поиска KCar (цены — в единицах источника, как в Encar)."""
        params: dict[str, str] = {}
        make = normalize_make(c.make) if c.make else None
        if make:
            params["brandName"] = make
        if c.model:
            params["modelName"] = c.model
        if c.year_from is not None:
            params["yearFrom"] = str(c.year_from)
        if c.year_to is not None:
            params["yearTo"] = str(c.year_to)
        if c.price_min is not None:
            params["priceFrom"] = str(int(c.price_min))
        if c.price_max is not None:
            params["priceTo"] = str(int(c.price_max))
        if c.mileage_max is not None:
            params["mileageTo"] = str(c.mileage_max)
        fuel = normalize_fuel(c.fuel)
        if fuel:
            params["fuel"] = fuel
        return params

    def _map_item(self, raw: dict[str, Any]) -> UnifiedListing | None:
        source_id = raw.get("carSeq")
        if source_id is None:
            return None

        price_man = raw.get("sellingPrice") or raw.get("price")
        price = (
            Decimal(str(price_man)) * self._price_unit if price_man is not None else None
        )

        photos = [
            f"{self._photo_prefix}/{str(p).lstrip('/')}"
            for p in (raw.get("photoList") or raw.get("photos") or [])
        ]

        year = raw.get("year") or raw.get("myear") or raw.get("modelYear")
        mileage = raw.get("mileage") or raw.get("mileageKm")
        return UnifiedListing(
            source=self.source_name,
            source_id=str(source_id),
            url=self._detail_template.format(source_id=source_id),
            title=raw.get("carName") or raw.get("title"),
            make=normalize_make(raw.get("brandName") or raw.get("makerName")),
            model=raw.get("modelName") or raw.get("carModel"),
            generation=raw.get("gradeName") or raw.get("trimName"),
            year=int(year) if year else None,
            price=price,
            currency="KRW",
            mileage_km=int(mileage) if mileage else None,
            vin=raw.get("vin") or None,
            body_type=raw.get("bodyName") or None,
            fuel=normalize_fuel(raw.get("fuelName") or raw.get("fuel")),
            transmission=normalize_transmission(raw.get("gearName") or raw.get("transmission")),
            color=raw.get("colorName") or None,
            location=raw.get("addr") or raw.get("officeAddr") or None,
            seller_type="dealer",
            images=photos,
            published_at=raw.get("registDate") or raw.get("createdAt"),
            raw=raw,
        )
