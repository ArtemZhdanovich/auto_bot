"""EncarApiAdapter — источник encar.com (Корея, API).

Encar Search API возвращает JSON вида:
    {"Count": 1234, "SearchResults": [{ "Id": ..., "Manufacturer": ..., ... }]}

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


class EncarApiAdapter(BaseApiAdapter):
    source_name = "encar"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._base_url = self.config.get("base_url", DEFAULT_BASE_URL)
        self._photo_prefix = self.config.get("photo_prefix", DEFAULT_PHOTO_PREFIX).rstrip("/")
        self._detail_template = self.config.get("detail_url_template", DEFAULT_DETAIL_TEMPLATE)
        self._page_size = int(self.config.get("page_size", DEFAULT_PAGE_SIZE))

    # --- fetch ---------------------------------------------------------

    async def fetch(
        self, criteria: SearchCriteria, cursor: str | None = None
    ) -> FetchResult:
        page = int(cursor) if cursor else 0
        payload = await self.http.get_json(
            self._base_url,
            params={"count": "true", "q": self._build_q(criteria)},
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
            await self.http.get_json(
                self._base_url,
                params={"count": "true", "q": "(And.Hidden.N.)"},
            )
            return True
        except Exception:
            return False

    # --- helpers -------------------------------------------------------

    def _build_q(self, c: SearchCriteria) -> str:
        """Формирование q-фильтра Encar: (And.Hidden.N.)(And Make.Hyundai. Model.X.)."""
        terms = ["(And.Hidden.N.)"]

        and_parts = []
        make = normalize_make(c.make) if c.make else None
        if make:
            and_parts.append(f"Make.{make}.")
        if c.model:
            and_parts.append(f"Model.{c.model}.")
        if and_parts:
            terms.append("(And " + " ".join(and_parts) + ")")

        if c.price_min is not None or c.price_max is not None:
            price_min = int(c.price_min) if c.price_min is not None else 0
            price_max = int(c.price_max) if c.price_max is not None else ""
            terms.append(f"(And.Price.{price_min}.{price_max}.)")

        if c.year_from is not None or c.year_to is not None:
            year_from = c.year_from if c.year_from is not None else 0
            year_to = c.year_to if c.year_to is not None else ""
            terms.append(f"(And.Year.{year_from}.{year_to}.)")

        if c.mileage_max is not None:
            terms.append(f"(And.Mileage.0.{c.mileage_max}.)")

        fuel = normalize_fuel(c.fuel)
        if fuel:
            terms.append(f"(And.FuelType.{fuel}.)")

        return "".join(terms)

    def _map_item(self, raw: dict[str, Any]) -> UnifiedListing | None:
        source_id = raw.get("Id")
        if source_id is None:
            return None

        price_man = raw.get("Price")
        price = Decimal(str(price_man)) * 10_000 if price_man is not None else None

        photos = [
            f"{self._photo_prefix}/{str(p).lstrip('/')}"
            for p in raw.get("Photos", [])
        ]

        year = raw.get("Year") or raw.get("FormYear")
        return UnifiedListing(
            source=self.source_name,
            source_id=str(source_id),
            url=self._detail_template.format(source_id=source_id),
            title=raw.get("Subject"),
            make=normalize_make(raw.get("Manufacturer")),
            model=raw.get("Model"),
            generation=raw.get("Badge") or raw.get("BadgeUnchanged"),
            year=int(year) if year else None,
            price=price,
            currency="KRW",
            mileage_km=int(raw["Mileage"]) if raw.get("Mileage") else None,
            vin=raw.get("Vin") or None,
            body_type=raw.get("BodyType") or None,
            fuel=normalize_fuel(raw.get("FuelType")),
            transmission=normalize_transmission(raw.get("Transmission")),
            color=raw.get("Color") or None,
            location=raw.get("OfficeCity") or None,
            seller_type="dealer",
            images=photos,
            published_at=raw.get("PhotoDate"),
            raw=raw,
        )
