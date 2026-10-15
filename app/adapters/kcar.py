"""KCarApiAdapter — источник kcar.com (Корея, API, шифрованный POST).

Поисковый API kcar.com (актуален на 10.2026):
    POST https://api.kcar.com/bc/search/list
    тело: {"enc": "<base64(AES-CBC(JSON-параметров))>"}
    ответ: {"message": null, "data": {"totalCnt": N, "rows": [...]}}

Шифрование воспроизведено из фронтового бандла (www.kcar.com/_nuxt/*.js,
`setEncDef`): AES-CBC, key "SKFJ2424DasfaJRI", iv "sfq241sf3dscs321",
PKCS7, результат — base64. Заголовки Referer/UA обязательны, иначе 404.

Пагинация: ключ страницы в API не найден (page/pageNo/... игнорируются —
проверено запросами), адаптер забирает top-N свежих объявлений одним
запросом (`limit`; проверено до 100). Фильтры — ключи с префиксом `wr_`
(wr_in_mnuftr_nm, wr_gt_mfg_dt, ... — схема из бандла страницы поиска).

Цены в API — в манах (1 ман = 10 000 KRW). Карта полей строки rows:
carCd/prc/milg/prdcnYr/mnuftrNm/modelNm/grdNm/fuelNm/trnsmsnNm/cntrRgnNm/
lsizeImgPath. Детальная карточка: /bc/detail/CarInfoDtl?carCd=<carCd>.
"""

import base64
import json
from decimal import Decimal
from typing import Any

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7

from app.adapters.base import BaseApiAdapter
from app.adapters.common.normalize import (
    normalize_fuel,
    normalize_make,
    normalize_transmission,
)
from app.schemas.criteria import SearchCriteria
from app.schemas.listing import FetchResult, UnifiedListing

DEFAULT_BASE_URL = "https://api.kcar.com/bc/search/list"
DEFAULT_DETAIL_TEMPLATE = "https://www.kcar.com/bc/detail/CarInfoDtl?carCd={source_id}"
DEFAULT_PAGE_SIZE = 100
DEFAULT_USD_KRW = 1400  # запасной курс, если criteria.price в USD

# Ключ/iv шифрования параметров — из фронтового бандла (setEncDef).
_ENC_KEY = b"SKFJ2424DasfaJRI"
_ENC_IV = b"sfq241sf3dscs321"

# Канонические EN-имена (normalize_make) → корейские для фильтра
# wr_in_mnuftr_nm (латиницу API не матчит — как у encar).
KCAR_MAKE_NAMES: dict[str, str] = {
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

# Популярные модели → wr_in_model_nm (корейский).
KCAR_MODEL_NAMES: dict[str, str] = {
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
    "x3": "X3",
    "x5": "X5",
    "e-class": "E클래스",
    "s-class": "S클래스",
    "c-class": "C클래스",
    "5 series": "5시리즈",
    "3 series": "3시리즈",
    "camry": "캠리",
    "corolla": "코롤라",
}

# Подтверждённый код топлива: из живого ответа fuelCd="001" → 가솔린 (petrol).
# Остальные коды не подтверждены — фильтр по ним не отправляем
# (результат шире, чем неверный фильтр).
KCAR_FUEL_CODES: dict[str, str] = {"petrol": "001"}


def _encrypt_params(params: dict[str, Any]) -> str:
    """JSON-параметры → base64(AES-128-CBC(PKCS7)) — как setEncDef во фронте."""
    padder = PKCS7(128).padder()
    padded = padder.update(json.dumps(params, ensure_ascii=False).encode()) + padder.finalize()
    encryptor = Cipher(algorithms.AES(_ENC_KEY), modes.CBC(_ENC_IV)).encryptor()
    return base64.b64encode(encryptor.update(padded) + encryptor.finalize()).decode()


class KCarApiAdapter(BaseApiAdapter):
    source_name = "kcar"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._base_url = self.config.get("base_url", DEFAULT_BASE_URL)
        self._detail_template = self.config.get("detail_url_template", DEFAULT_DETAIL_TEMPLATE)
        self._page_size = int(self.config.get("page_size", DEFAULT_PAGE_SIZE))
        self._price_unit = int(self.config.get("price_unit", 10_000))
        self._usd_krw = int(self.config.get("usd_krw", DEFAULT_USD_KRW))

    # --- fetch ---------------------------------------------------------

    async def fetch(
        self, criteria: SearchCriteria, cursor: str | None = None
    ) -> FetchResult:
        params: dict[str, Any] = {"limit": self._page_size, **self._build_params(criteria)}
        payload = await self.http.post_json(
            self._base_url, json={"enc": _encrypt_params(params)}
        )
        data = payload.get("data") or {}
        raw_items = data.get("rows") or []
        items = [listing for item in raw_items if (listing := self._map_item(item))]
        total = int(data.get("totalCnt", len(items)))

        # Пагинации в API нет (ключ страницы игнорируется) — одна страница.
        return FetchResult(
            items=items,
            next_cursor=None,
            has_more=False,
            source_meta={"total": total, "fetched": len(items)},
        )

    async def healthcheck(self) -> bool:
        try:
            await self.http.post_json(
                self._base_url, json={"enc": _encrypt_params({"limit": 1})}
            )
            return True
        except Exception:
            return False

    # --- helpers -------------------------------------------------------

    def _build_params(self, c: SearchCriteria) -> dict[str, str]:
        """Критерии → wr_-фильтры API (цены — в манах, годы — 'YYYYMM')."""
        params: dict[str, str] = {}

        make = normalize_make(c.make) if c.make else None
        if make and (kr := KCAR_MAKE_NAMES.get(make)):
            params["wr_in_mnuftr_nm"] = kr
        if c.model and (kr_model := KCAR_MODEL_NAMES.get(c.model.lower().strip())):
            params["wr_in_model_nm"] = kr_model
        if c.year_from is not None:
            params["wr_gt_mfg_dt"] = f"{c.year_from}01"
        if c.year_to is not None:
            params["wr_lt_mfg_dt"] = f"{c.year_to}12"

        price_from_man, price_to_man = self._price_range_man(c)
        if price_from_man > 0:
            params["wr_gt_prc"] = str(price_from_man)
        if price_to_man:
            params["wr_lt_prc"] = str(price_to_man)

        if c.mileage_max is not None:
            params["wr_lt_milg"] = str(int(c.mileage_max))

        fuel = normalize_fuel(c.fuel)
        if fuel and (code := KCAR_FUEL_CODES.get(fuel)):
            params["wr_in_fuel_cd"] = code
        return params

    def _price_range_man(self, c: SearchCriteria) -> tuple[int, int]:
        """Цена критериев → диапазон в манах (1 ман = 10 000 KRW)."""
        currency = (c.currency or "KRW").upper()
        rate = self._usd_krw if currency == "USD" else 1
        lo = int(float(c.price_min) * rate / 10_000) if c.price_min is not None else 0
        hi = int(float(c.price_max) * rate / 10_000) if c.price_max is not None else 0
        return max(lo, 0), max(hi, 0)

    def _map_item(self, raw: dict[str, Any]) -> UnifiedListing | None:
        source_id = raw.get("carCd")
        if not source_id:
            return None

        price_man = raw.get("prc")
        price = Decimal(str(price_man)) * self._price_unit if price_man else None

        year = raw.get("prdcnYr")
        mileage = raw.get("milg")
        photos = [raw[key] for key in ("lsizeImgPath",) if raw.get(key)]

        generation = " ".join(
            part for part in (raw.get("grdNm"), raw.get("grdDtlNm")) if part
        ).strip() or None
        title = (
            raw.get("carWhlNm")
            or " ".join(
                p for p in (raw.get("mnuftrNm"), raw.get("modelNm"), generation) if p
            ).strip()
            or None
        )
        return UnifiedListing(
            source=self.source_name,
            source_id=str(source_id),
            url=self._detail_template.format(source_id=source_id),
            title=title,
            make=normalize_make(raw.get("mnuftrNm")),
            model=raw.get("modelNm") or None,
            generation=generation,
            year=int(year) if year else None,
            price=price,
            currency="KRW",
            mileage_km=int(mileage) if mileage else None,
            vin=None,  # VIN в списке нет; cno — госномер («61조3549»)
            body_type=raw.get("carctgrNm") or None,
            fuel=normalize_fuel(raw.get("fuelNm")),
            transmission=normalize_transmission(raw.get("trnsmsnNm")),
            color=raw.get("extrColorNm") or None,
            location=raw.get("cntrRgnNm") or None,
            seller_type="dealer",  # kcar — сеть собственных центров (직영)
            images=photos,
            published_at=None,  # даты публикации в списке нет
            raw=raw,
        )
