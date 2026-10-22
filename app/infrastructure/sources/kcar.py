"""KCarApiAdapter — источник kcar.com (Корея, API, шифрованный POST).

Поисковый API kcar.com (актуален на 10.2026):
    POST https://api.kcar.com/bc/search/list
    тело: {"enc": "<base64(AES-CBC(JSON-параметров))>"}
    ответ: {"message": null, "data": {"totalCnt": N, "rows": [...]}}

Шифрование воспроизведено из фронтового бандла (www.kcar.com/_nuxt/*.js,
`setEncDef`): AES-CBC, key "SKFJ2424DasfaJRI", iv "sfq241sf3dscs321",
PKCS7, результат — base64. Заголовки Referer/UA обязательны, иначе 404.

ВНИМАНИЕ: ключ шифрования — **публичный** (реверс из фронта), не секрет.
Он не даёт доступа к приватным данным KCar, только «обфусцирует» запросы
от простых ботов. Хранится в коде для совместимости с бандлом KCar.
При смене ключа на стороне KCar — обновить константу и задеплоить.

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
import logging
from decimal import Decimal, InvalidOperation
from typing import Any

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7

from app.infrastructure.sources.base import BaseApiAdapter, FetchError
from app.infrastructure.sources.common.normalize import (
    normalize_body,
    normalize_color,
    normalize_engine_volume,
    normalize_fuel,
    normalize_make,
    normalize_transmission,
)
from app.domain.search.criteria import SearchCriteria
from app.domain.listings.unified import FetchResult, UnifiedListing

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.kcar.com/bc/search/list"
DEFAULT_DETAIL_TEMPLATE = "https://www.kcar.com/bc/detail/CarInfoDtl?carCd={source_id}"
DEFAULT_PAGE_SIZE = 100
DEFAULT_USD_KRW = 1400  # запасной курс, если criteria.price в USD
# Жёсткий cap: даже если API вернёт больше — не просим больше одного раза
MAX_TOTAL_ITEMS = 10_000
_SUPPORTED_CURRENCIES = frozenset({"KRW", "USD"})

# Ключ/iv шифрования параметров — из фронтового бандла (setEncDef).
# PUBLIC: не является секретом, реверснут из публичного JS KCar.
_ENC_KEY = b"SKFJ2424DasfaJRI"
_ENC_IV = b"sfq241sf3dscs321"

# Заголовки, обязательные для API KCar (по докстрингу: без них 404).
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
    "Referer": "https://www.kcar.com/bc/search",
    "Origin": "https://www.kcar.com",
    "Content-Type": "application/json",
}

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
# (результат шире, чем неверный фильтр). Задокументировать в TECHDEBT.
KCAR_FUEL_CODES: dict[str, str] = {"petrol": "001"}


def _to_int_safe(value: Any) -> int | None:
    """Приводит значение к int без исключения.

    Args:
        value: Любое значение (int/float/str/None).

    Returns:
        `int` или `None`, если значение не приводится.
    """
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError, OverflowError):
        return None


def _to_decimal_safe(value: Any) -> Decimal | None:
    """Приводит значение к `Decimal` без исключения.

    Args:
        value: Любое значение (int/float/str/Decimal/None).

    Returns:
        `Decimal` или `None`, если значение невалидно.
    """
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _encrypt_params(params: dict[str, Any]) -> str:
    """JSON-параметры → base64(AES-128-CBC(PKCS7)) — как setEncDef во фронте.

    Args:
        params: Параметры запроса (JSON-сериализуемые).

    Returns:
        Base64-строка шифротекста.

    Raises:
        ValueError: Если `params` не сериализуется в JSON.
    """
    try:
        raw = json.dumps(params, ensure_ascii=False).encode()
    except (TypeError, ValueError) as exc:
        raise ValueError(f"kcar: params are not JSON-serializable: {exc}") from exc
    padder = PKCS7(128).padder()
    padded = padder.update(raw) + padder.finalize()
    encryptor = Cipher(algorithms.AES(_ENC_KEY), modes.CBC(_ENC_IV)).encryptor()
    return base64.b64encode(encryptor.update(padded) + encryptor.finalize()).decode()


class KCarApiAdapter(BaseApiAdapter):
    """Адаптер поискового API kcar.com.

    Attributes:
        source_name: `"kcar"`.
    """

    source_name = "kcar"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._base_url = self.config.get("base_url", DEFAULT_BASE_URL)
        self._detail_template = self.config.get("detail_url_template", DEFAULT_DETAIL_TEMPLATE)
        try:
            page_size = int(self.config.get("page_size", DEFAULT_PAGE_SIZE))
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"kcar: invalid page_size={self.config.get('page_size')!r}"
            ) from exc
        if page_size <= 0:
            raise ValueError(f"kcar: page_size must be > 0, got {page_size}")
        self._page_size = page_size
        try:
            self._price_unit = int(self.config.get("price_unit", 10_000))
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"kcar: invalid price_unit={self.config.get('price_unit')!r}"
            ) from exc
        if self._price_unit <= 0:
            raise ValueError(f"kcar: price_unit must be > 0, got {self._price_unit}")
        try:
            self._usd_krw = int(self.config.get("usd_krw", DEFAULT_USD_KRW))
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"kcar: invalid usd_krw={self.config.get('usd_krw')!r}"
            ) from exc
        if self._usd_krw <= 0:
            raise ValueError(f"kcar: usd_krw must be > 0, got {self._usd_krw}")

    # --- fetch ---------------------------------------------------------

    async def fetch(
        self, criteria: SearchCriteria, cursor: str | None = None
    ) -> FetchResult:
        """Забирает top-N объявлений (пагинации в API нет).

        Args:
            criteria: Критерии поиска (make/model/price/year/mileage/fuel).
            cursor: Игнорируется (API не поддерживает пагинацию).

        Returns:
            `FetchResult` с объявлениями; `has_more=False` всегда, но при
            `total > len(items)` пишется warning и в `source_meta` —
            флаг `truncated`.

        Raises:
            FetchError: При невалидном ответе API или фильтре,
                не поддерживаемом API.
        """
        if cursor:
            logger.warning("kcar_cursor_ignored cursor=%r (API has no pagination)", cursor)

        params: dict[str, Any] = {
            "limit": self._page_size,
            **self._build_params(criteria),
        }
        payload = await self.http.post_json(
            self._base_url,
            json={"enc": _encrypt_params(params)},
            headers=_HEADERS,
        )
        if not isinstance(payload, dict):
            raise FetchError(
                f"kcar: unexpected payload type {type(payload).__name__}"
            )

        data = payload.get("data")
        if not isinstance(data, dict):
            data = {}
        raw_items = data.get("rows") or []
        if not isinstance(raw_items, list):
            raise FetchError("kcar: data.rows is not a list")

        items: list[UnifiedListing] = []
        for item in raw_items:
            if not isinstance(item, dict):
                logger.warning("kcar_non_dict_item: %r", type(item).__name__)
                continue
            listing = self._map_item(item)
            if listing is not None:
                items.append(listing)

        total = _to_int_safe(data.get("totalCnt")) or len(items)
        effective_total = min(total, MAX_TOTAL_ITEMS)
        truncated = effective_total > len(items)
        if truncated:
            logger.warning(
                "kcar_truncated total=%d fetched=%d (API has no pagination)",
                total, len(items),
            )

        return FetchResult(
            items=items,
            next_cursor=None,
            has_more=False,
            source_meta={
                "total": total,
                "fetched": len(items),
                "truncated": truncated,
            },
        )

    async def healthcheck(self) -> bool:
        """Проверяет, что API отвечает валидным `data.rows`.

        Returns:
            `True`, если ответ — `{"data": {"rows": [...]}}` (пустой `rows`
            допустим — источник жив, просто нет данных).
        """
        try:
            payload = await self.http.post_json(
                self._base_url,
                json={"enc": _encrypt_params({"limit": 1})},
                headers=_HEADERS,
            )
        except Exception:  # noqa: BLE001
            return False
        if not isinstance(payload, dict):
            return False
        data = payload.get("data")
        if not isinstance(data, dict):
            logger.warning("kcar_healthcheck_no_data payload_keys=%s", list(payload))
            return False
        if "rows" not in data:
            logger.warning("kcar_healthcheck_no_rows data_keys=%s", list(data))
            return False
        return True

    # --- helpers -------------------------------------------------------

    def _build_params(self, c: SearchCriteria) -> dict[str, str]:
        """Критерии → wr_-фильтры API (цены — в манах, годы — 'YYYYMM').

        Если запрошена марка/модель, отсутствующая в словаре, —
        `FetchError` (а не молчаливый отброс фильтра: иначе API вернёт
        весь каталог под видом отфильтрованного).

        Args:
            c: Критерии поиска.

        Returns:
            Словарь `wr_*`-параметров.

        Raises:
            FetchError: При неподдерживаемой марке/модели/валюте,
                `price_min > price_max`, `year_from > year_to`.
        """
        params: dict[str, str] = {}

        make = normalize_make(c.make) if c.make else None
        if make:
            kr = KCAR_MAKE_NAMES.get(make)
            if kr is None:
                raise FetchError(
                    f"kcar: manufacturer {make!r} is not supported by the API"
                )
            params["wr_in_mnuftr_nm"] = kr
        if c.model:
            kr_model = KCAR_MODEL_NAMES.get(c.model.lower().strip())
            if kr_model is None:
                raise FetchError(
                    f"kcar: model {c.model!r} is not supported by the API"
                )
            params["wr_in_model_nm"] = kr_model

        year_from = _to_int_safe(c.year_from)
        year_to = _to_int_safe(c.year_to)
        if year_from is not None and year_to is not None and year_from > year_to:
            raise FetchError(f"kcar: year_from ({year_from}) > year_to ({year_to})")
        if year_from is not None:
            params["wr_gt_mfg_dt"] = f"{year_from}01"
        if year_to is not None:
            params["wr_lt_mfg_dt"] = f"{year_to}12"

        price_from_man, price_to_man = self._price_range_man(c)
        if price_from_man > 0:
            params["wr_gt_prc"] = str(price_from_man)
        if price_to_man:
            params["wr_lt_prc"] = str(price_to_man)

        mileage_max = _to_int_safe(c.mileage_max)
        if mileage_max is not None and mileage_max > 0:
            params["wr_lt_milg"] = str(mileage_max)

        fuel = normalize_fuel(c.fuel)
        if fuel and (code := KCAR_FUEL_CODES.get(fuel)):
            params["wr_in_fuel_cd"] = code
        elif fuel:
            # Код не подтверждён — фильтр не отправляем (результат шире,
            # но не неверный). Логируем для наблюдаемости.
            logger.info("kcar_fuel_filter_skipped fuel=%s (code not confirmed)", fuel)

        return params

    def _price_range_man(self, c: SearchCriteria) -> tuple[int, int]:
        """Цена критериев → диапазон в манах (1 ман = 10 000 KRW).

        Args:
            c: Критерии поиска.

        Returns:
            Кортеж `(lo, hi)` в манах, `0 <= lo <= hi`.

        Raises:
            FetchError: При неподдерживаемой валюте или `price_min > price_max`.
        """
        currency = (c.currency or "KRW").upper()
        if currency not in _SUPPORTED_CURRENCIES:
            raise FetchError(
                f"kcar: currency {currency!r} is not supported "
                f"(expected one of {sorted(_SUPPORTED_CURRENCIES)})"
            )
        rate = Decimal(self._usd_krw) if currency == "USD" else Decimal(1)

        if c.price_min is not None and c.price_max is not None and c.price_min > c.price_max:
            raise FetchError(
                f"kcar: price_min ({c.price_min}) > price_max ({c.price_max})"
            )

        def to_man(value: Decimal) -> int:
            return int((value * rate / Decimal(10_000)).to_integral_value())

        lo = to_man(Decimal(str(c.price_min))) if c.price_min is not None else 0
        hi = to_man(Decimal(str(c.price_max))) if c.price_max is not None else 0
        return max(lo, 0), max(hi, 0)

    def _map_item(self, raw: dict[str, Any]) -> UnifiedListing | None:
        """Преобразует один элемент `rows` в `UnifiedListing`.

        Любая ошибка парсинга поля (price/mileage/year) логируется и
        приводит к `None` для этого объявления — не роняет `fetch`.

        Args:
            raw: Словарь объявления из API.

        Returns:
            `UnifiedListing` или `None`.
        """
        # carCd — не число: "EC61405190", "RC148047". Числовое приведение
        # отбрасывало все объявления (0 items при total=8857).
        source_id = str(raw.get("carCd") or "").strip()
        # Реальные id: EC61405190, RC148047, иногда только цифры.
        # Мусор без цифр («abc») — битая карточка.
        if not source_id or not any(ch.isdigit() for ch in source_id):
            return None

        price_man = _to_decimal_safe(raw.get("prc"))
        price = price_man * self._price_unit if price_man is not None else None

        year = _to_int_safe(raw.get("prdcnYr"))
        mileage = _to_int_safe(raw.get("milg"))
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

        body_raw = raw.get("carctgrNm")
        color_raw = raw.get("extrColorNm")

        return UnifiedListing(
            source=self.source_name,
            source_id=str(source_id),
            url=self._detail_template.format(source_id=source_id),
            title=title,
            make=normalize_make(raw.get("mnuftrNm")),
            model=raw.get("modelNm") or None,
            generation=generation,
            year=year,
            price=price,
            currency="KRW",
            mileage_km=mileage,
            vin=None,  # VIN в списке нет; cno — госномер («61조3549»)
            body_type=normalize_body(body_raw) or body_raw or None,
            fuel=normalize_fuel(raw.get("fuelNm")),
            transmission=normalize_transmission(raw.get("trnsmsnNm")),
            # engdispmnt обычно пуст в списке (заполнен в detail-of-ng,
            # см. enrich_listing_specs) — берём если есть.
            engine_volume=normalize_engine_volume(raw.get("engdispmnt")),
            color=normalize_color(color_raw) or color_raw or None,
            location=raw.get("cntrRgnNm") or None,
            seller_type="dealer",  # kcar — сеть собственных центров (직영)
            images=photos,
            published_at=None,  # даты публикации в списке нет
            raw=raw,
        )