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

import asyncio
import logging
import re
from decimal import Decimal, InvalidOperation
from typing import Any

from app.infrastructure.sources.base import BaseApiAdapter, FetchError
from app.infrastructure.sources.common.normalize import (
    normalize_body,
    normalize_color,
    normalize_fuel,
    normalize_make,
    normalize_transmission,
)
from app.domain.search.criteria import SearchCriteria
from app.domain.listings.unified import FetchResult, UnifiedListing

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.encar.com/search/car/list/general"
DEFAULT_PHOTO_PREFIX = "https://ci.encar.com"
DEFAULT_DETAIL_TEMPLATE = "https://fem.encar.com/car/detail/{source_id}"
# Детальный карточный API (окт 2026, анонимно): VIN (у заполненных), полный
# spec (объём в см³), ПОЛНЫЙ набор фото (search отдаёт только часть),
# diagnosisCar/осмотр/обременения. Удалённое с площадки объявление → 404.
DEFAULT_DETAIL_API = "https://api.encar.com/v1/readside/vehicle/{source_id}"
# Флаг «безаварийности» (Clean Encar): {"vehicleId": N, "cleaned": bool}.
DEFAULT_CLEAN_API = "https://api.encar.com/v1/readside/clean-encar/vehicle/{source_id}"
# Чек осмотра (성능점검기록부): зоны кузова (outers), чек-лист агрегатов
# (inners), фото чека (images), флаги ДТП/ремонта (master). Анонимно 200,
# для авто без чека — 404 (окт 2026, найдено в JS-бандле fem.encar.com).
DEFAULT_INSPECTION_API = "https://api.encar.com/v1/readside/inspection/vehicle/{source_id}"
# Кто проводил чек (название центра + инспектор) — поле `inspName`.
DEFAULT_INSPECTION_SUMMARY_API = (
    "https://api.encar.com/v1/readside/inspection/vehicle/{source_id}/summary"
)
# Страховая история: количество ДТП (своя/чужая вина), утопления, угоны,
# смена владельцев/номеров, залог/лизинг (анонимно 200).
DEFAULT_RECORD_API = "https://api.encar.com/v1/readside/record/vehicle/{source_id}/summary"
# 엔ка진단 — вердикт диагностики: зоны кузова NORMAL/дефект + комментарии
# (CHECKER_COMMENT «무사고» и пр.). Анонимно 200, без диагностики — 404.
DEFAULT_DIAGNOSIS_API = "https://api.encar.com/v1/readside/diagnosis/vehicle/{source_id}"
# Параллельные detail-запросы на страницу выдачи (вежливость к API).
_DETAIL_CONCURRENCY = 5
# VIN: 17 знаков без I/O/Q (ISO 3779). Корейские VIN соответствуют; мусор
# («수입차», госномер) не должен попадать в listings.vin.
_VIN_RE = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")
DEFAULT_PAGE_SIZE = 20
DEFAULT_SORT = "ModifiedDate"
DEFAULT_USD_KRW = 1400  # запасной курс, если criteria.price в USD
# Жёсткий cap на пагинацию: защита от «Count=1M» и бесконечного has_more.
MAX_TOTAL_ITEMS = 10_000
# Валюты, которые поддерживаются явно (остальные — ошибка).
_SUPPORTED_CURRENCIES = frozenset({"KRW", "USD"})

# Комплектация (миграция 0023): коды detail.options → RU-названия.
# Пространство кодов — классическая таблица Encar (проверено на выдаче:
# 010 선루프, 009 통풍시트, 095 HUD, 087 360°-뷰 совпадают с oneLineText
# продавцов). Коды 001–008 в словаре fem-чеклиста пересекаются с новыми
# кодами (фото-лейблы) — здесь зафиксированы «осмысленные» значения
# (подогрев/память сидений, а не «ЭСП»/«ГУР», которые есть у всех).
# Неизвестные коды (1xxx choice и прочие) молча пропускаем — сырые коды
# не показываем.
_STANDARD_OPTION_RU: dict[str, str] = {
    "001": "ABS",
    "002": "Подушки безопасности",
    "003": "Парктроник",
    "004": "Круиз-контроль",
    "005": "Навигация",
    "006": "Электрорегулировка сидений",
    "007": "Подогрев сидений",
    "008": "Память сидений",
    "009": "Вентиляция сидений",
    "010": "Люк",
    "014": "Кожаный салон",
    "015": "Дистанционное открытие дверей",
    "017": "Легкосплавные диски",
    "019": "Антипробуксовочная система (TCS)",
    "020": "Боковые подушки безопасности",
    "023": "Климат-контроль",
    "024": "Электроскладные зеркала",
    "030": "Электрохромное зеркало",
    "031": "Управление мультимедиа на руле",
    "033": "Датчики давления в шинах",
    "054": "Мониторы для задних пассажиров",
    "055": "Система курсовой устойчивости (ESC)",
    "056": "Подушки-шторки безопасности",
    "057": "Смарт-ключ",
    "058": "Камера заднего вида",
    "059": "Электропривод багажника",
    "062": "Рейлинги на крыше",
    "071": "AUX-разъём",
    "072": "USB-порты",
    "074": "Транспондер Hi-Pass",
    "080": "Доводчики дверей",
    "081": "Датчик дождя",
    "082": "Подогрев руля",
    "083": "Электрорегулировка руля",
    "084": "Подрулевые лепестки",
    "086": "Контроль слепых зон",
    "087": "Круговой обзор 360°",
    "088": "Контроль полосы движения",
    "089": "Электрорегулировка задних сидений",
    "090": "Вентиляция задних сидений",
    "091": "Массаж сидений",
    "094": "Электронный стояночный тормоз",
    "095": "Проекционный дисплей (HUD)",
    "096": "Bluetooth",
    "097": "Автоматическое освещение",
}

# Тюнинг — отдельное пространство кодов (options.tuning), словарь
# `/v1/readside/vehicles/car/options/tuning`.
_TUNING_OPTION_RU: dict[str, str] = {
    "001": "Перекраска крыши",
    "002": "Виниловая плёнка",
    "003": "Аэродинамический обвес",
    "004": "Спойлер",
    "005": "Боковые подножки",
    "006": "LED-стоп-сигналы",
    "007": "Фаркоп",
    "008": "Сиденья-лимузин",
    "009": "Ковшеобразные сиденья",
    "010": "Спортивный руль (D-cut)",
    "011": "LED-подсветка салона",
    "012": "Мультимедийная голова",
    "013": "Акустика/усилитель/сабвуфер",
    "014": "Шумоизоляция салона",
    "015": "Спортивный впуск",
    "016": "Тюнинг выхлопа",
    "017": "Спортивный глушитель",
    "018": "Прошивка ECU",
    "019": "Стабилизаторы поперечной устойчивости",
    "020": "Передняя растяжка (strut bar)",
    "021": "Усиленные тормоза",
    "022": "Спортивные педали",
    "023": "Видеорегистратор",
    "024": "Кнопка запуска двигателя",
}

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
    # ВНИМАНИЕ: значение фасета в API — `LPG(일반인 구입)`. Символ `)`
    # внутри значения ломает синтаксис `(And...)`. Требует проверки на
    # живом API: либо API принимает `_` как escape, либо фильтр не работает.
    # См. TECHDEBT.md, пункт про LPG-фасет.
    "lpg": "LPG(일반인 구입_)",
}

# unified-transmission → Transmission в API
ENCAR_TRANSMISSION_NAMES: dict[str, str] = {
    "automatic": "오토",
    "manual": "수동",
    "cvt": "CVT",
}


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


# --- маппинг чека/диагностики Encar на unified-схему отчёта ---------------

# Зоны 엔ка진단 (стабильные EN-имена) → BodyZone миниаппа.
# Значение None — зоны нет в нашей схеме (задний бампер) — пропускаем.
_DIAGNOSIS_ZONES: dict[str, str | None] = {
    "FRONT_BUMPER": "front_bumper",
    "REAR_BUMPER": None,
    "HOOD": "hood",
    "ROOF": "roof",
    "ROOF_PANEL": "roof",
    "TRUNK_LID": "trunk",
    "TAIL_GATE": "trunk",
    "REAR_PANEL": "trunk",
    "FRONT_FENDER_LEFT": "fender_fl",
    "FRONT_FENDER_RIGHT": "fender_fr",
    "REAR_FENDER_LEFT": "fender_rl",
    "REAR_FENDER_RIGHT": "fender_rr",
    "QUARTER_PANEL_LEFT": "fender_rl",
    "QUARTER_PANEL_RIGHT": "fender_rr",
    "FRONT_DOOR_LEFT": "door_fl",
    "FRONT_DOOR_RIGHT": "door_fr",
    "BACK_DOOR_LEFT": "door_rl",
    "BACK_DOOR_RIGHT": "door_rr",
    "ROCKER_PANEL_LEFT": "rocker_l",
    "ROCKER_PANEL_RIGHT": "rocker_r",
}

# Комментарий-зоны 엔ка진단 (текст, не отметка на карте).
_DIAGNOSIS_COMMENT_KEYS = frozenset({"CHECKER_COMMENT", "OUTER_PANEL_COMMENT"})

# Тип дефекта по корейскому статусу (판금/도색/…). Порядок = приоритет.
_MARK_TYPES_KR: tuple[tuple[str, str], ...] = (
    ("교환", "replace"),  # замена элемента
    ("판금", "dent"),  # рихтовка
    ("도색", "repaint"),  # покраска
    ("부식", "rust"),  # коррозия
    ("녹", "rust"),
    ("균열", "crack"),  # трещина
    ("깨짐", "glass"),  # повреждение стекла
    ("흠집", "scratch"),  # царапина
    ("스크래치", "scratch"),
)
# Неизвестный статус — самая «мягкая» отметка (видно на карте, не пугает).
_MARK_TYPE_FALLBACK = "scratch"

# Группы чека (성능점검기록부, стабильные коды S00–S07) → ItemKey.
# S00 — самодиагностика продавца, дублирует S01/S02, не маппим.
_ITEM_GROUP_KEYS: dict[str, str | None] = {
    "S00": None,
    "S01": "engine",  # 원동기 (двигатель)
    "S02": "transmission",  # 변속기 (КПП)
    "S03": "transmission",  # 동력전달 (приводы/дифференциал)
    "S04": "steering",  # 조향 (рулевое)
    "S05": "brakes",  # 제동 (тормоза)
    "S06": "electronics",  # 전기 (электрика)
    "S07": "leaks",  # 연료 (топливная система, течи)
}

# Статус пункта чека: код → (ItemStatus, русская подпись для note).
# 1 양호 норма · 2 적정 допустимо · 3 없음 отсутствует (норма для течей) ·
# 10 불량 неисправен · 4/6 микроподтекание · 5/7 подтекание · 8 недостаточно.
_ITEM_STATUSES: dict[str, tuple[str, str]] = {
    "1": ("ok", "норма"),
    "2": ("ok", "допустимо"),
    "3": ("ok", "нет"),
    "10": ("defect", "неисправен"),
    "4": ("warn", "микроподтекание"),
    "6": ("warn", "запотевание"),
    "8": ("warn", "недостаточно"),
    "5": ("defect", "подтекание"),
    "7": ("defect", "подтекание"),
}
_ITEM_WORST: dict[str, int] = {"ok": 0, "warn": 1, "defect": 2}

# Порядок ключей чек-листа в отчёте (как на фронте).
_ITEM_ORDER: tuple[str, ...] = (
    "engine", "transmission", "suspension", "brakes", "steering",
    "electronics", "interior", "optics", "glass", "tires", "ac", "leaks",
)


def _mark_type_from_kr(title: Any) -> str:
    """Определяет MarkType по корейскому тексту статуса/результата.

    Args:
        title: Текст статуса (예: «도색», «판금», «정상»).

    Returns:
        Имя MarkType из схемы отчёта.
    """
    text = str(title or "")
    for needle, mark in _MARK_TYPES_KR:
        if needle in text:
            return mark
    return _MARK_TYPE_FALLBACK


def _status_of(status_type: Any) -> tuple[str, str] | None:
    """Код статуса пункта чека → (ItemStatus, русская подпись).

    Args:
        status_type: Значение `statusType` из чека (dict или None).

    Returns:
        Кортеж (status, label) или `None`, если статус не указан.
    """
    if not isinstance(status_type, dict):
        return None
    code = str(status_type.get("code") or "")
    if not code:
        return None
    mapped = _ITEM_STATUSES.get(code)
    if mapped is None:
        # Незнакомый код — осторожно считаем предупреждением.
        return ("warn", str(status_type.get("title") or ""))
    return mapped


def _outer_zone(title: Any) -> str | None:
    """Корейское название зоны чека (outer) → BodyZone (или None).

    Названия вида «프론트 범퍼», «앞도어(좁)». Определяем по ключевым словам;
    неизвестные зоны пропускаем (схема кузова фиксирована).

    Args:
        title: Корейский текст названия зоны.

    Returns:
        Имя BodyZone или `None`.
    """
    t = str(title or "")
    if not t:
        return None
    left = "좌" in t
    front = ("앞" in t) or ("프론트" in t) or ("전방" in t)
    rear = ("뒤" in t) or ("리어" in t) or ("후방" in t)
    if "범퍼" in t:
        return "front_bumper" if front or not rear else None
    if "보닛" in t:
        return "hood"
    if "트렁크" in t or "게이트" in t or "리어패널" in t:
        return "trunk"
    if "루프" in t:
        return "roof"
    if "도어" in t:
        return ("door_fl" if left else "door_fr") if front else (
            "door_rl" if left else "door_rr"
        )
    if "펜더" in t or "쿼터" in t or "파일러" in t:
        return ("fender_fl" if left else "fender_fr") if front or not rear else (
            "fender_rl" if left else "fender_rr"
        )
    if "록커" in t or "미어" in t:
        return "rocker_l" if left else "rocker_r"
    return None


class EncarApiAdapter(BaseApiAdapter):
    """Адаптер Encar Search API.

    Attributes:
        source_name: `"encar"`.
    """

    source_name = "encar"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._base_url = self.config.get("base_url", DEFAULT_BASE_URL)
        self._photo_prefix = self.config.get("photo_prefix", DEFAULT_PHOTO_PREFIX).rstrip("/")
        self._detail_template = self.config.get("detail_url_template", DEFAULT_DETAIL_TEMPLATE)
        try:
            page_size = int(self.config.get("page_size", DEFAULT_PAGE_SIZE))
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"encar: invalid page_size={self.config.get('page_size')!r}"
            ) from exc
        if page_size <= 0:
            # Молчаливый max(1, 0) скрывал мисконфиг: «1 объявление на
            # страницу» вместо явной ошибки конфигурации.
            raise ValueError(f"encar: page_size must be > 0, got {page_size}")
        self._page_size = page_size
        self._sort = self.config.get("sort", DEFAULT_SORT)
        try:
            self._usd_krw = float(self.config.get("usd_krw", DEFAULT_USD_KRW))
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"encar: invalid usd_krw={self.config.get('usd_krw')!r}"
            ) from exc
        if self._usd_krw <= 0:
            raise ValueError(f"encar: usd_krw must be > 0, got {self._usd_krw}")
        self._detail_api = self.config.get("detail_api_template", DEFAULT_DETAIL_API)
        self._clean_api = self.config.get("clean_api_template", DEFAULT_CLEAN_API)
        self._inspection_api = self.config.get(
            "inspection_api_template", DEFAULT_INSPECTION_API
        )
        self._inspection_summary_api = self.config.get(
            "inspection_summary_api_template", DEFAULT_INSPECTION_SUMMARY_API
        )
        self._record_api = self.config.get("record_api_template", DEFAULT_RECORD_API)
        self._diagnosis_api = self.config.get(
            "diagnosis_api_template", DEFAULT_DIAGNOSIS_API
        )
        self._enrich_details = bool(self.config.get("enrich_details", True))

    # --- fetch ---------------------------------------------------------

    async def fetch(
        self, criteria: SearchCriteria, cursor: str | None = None
    ) -> FetchResult:
        """Забирает страницу объявлений.

        Args:
            criteria: Критерии поиска (make/model/price/year/mileage/fuel/transmission).
            cursor: Номер страницы строкой (`"0"`, `"1"`, …) или `None`.

        Returns:
            `FetchResult` с объявлениями; `has_more` учитывает `Count` от API.

        Raises:
            FetchError: При невалидном курсоре, невалидном ответе или
                сломанном контракте (Count > 0, но пустой SearchResults).
        """
        page = self._parse_cursor(cursor)
        payload = await self.http.get_json(
            self._base_url, params=self._build_params(self._build_q(criteria), page)
        )
        if not isinstance(payload, dict):
            raise FetchError(
                f"encar: unexpected payload type {type(payload).__name__}"
            )
        # `.get(..., [])` не спасает от явного null — используем `or []`.
        raw_items = payload.get("SearchResults") or []
        if not isinstance(raw_items, list):
            raise FetchError("encar: SearchResults is not a list")

        items: list[UnifiedListing] = []
        for item in raw_items:
            if not isinstance(item, dict):
                logger.warning("encar_non_dict_item: %r", type(item).__name__)
                continue
            listing = self._map_item(item)
            if listing is not None:
                items.append(listing)

        total = _to_int_safe(payload.get("Count")) or len(items)
        # Сломанный контракт: Count > 0, но API вернул пустоту.
        if total > 0 and not items and page == 0:
            logger.warning(
                "encar_broken_contract: count=%d but empty SearchResults", total
            )

        # Cap: не уходим в бесконечную пагинацию, если API врёт с Count.
        effective_total = min(total, MAX_TOTAL_ITEMS)
        has_more = bool(items) and (page + 1) * self._page_size < effective_total

        # Обогащение детальными карточками (VIN, объём, фото, осмотр).
        # Ошибка/404 отдельной карточки не роняет страницу: у объявления
        # остаются данные search-выдачи.
        if items and self._enrich_details:
            await self._enrich_from_details(items)

        return FetchResult(
            items=items,
            next_cursor=str(page + 1) if has_more else None,
            has_more=has_more,
            source_meta={"total": total, "page": page},
        )

    async def healthcheck(self) -> bool:
        """Проверяет, что API отвечает и не сломан контракт.

        Сломанный контракт (Count > 0, но пустой SearchResults) — `False`,
        даже если HTTP-ответ 200. Пустой `Count=0` — `True` (источник жив,
        просто нет данных).

        Returns:
            `True`, если источник отвечает и контракт не сломан.
        """
        try:
            payload = await self.http.get_json(
                self._base_url, params=self._build_params("(And.Hidden.N.)", 0)
            )
        except Exception:  # noqa: BLE001 — healthcheck не должен ронять вызывающего
            return False
        # Валидный ответ обязан содержать Count.
        if not isinstance(payload, dict) or "Count" not in payload:
            logger.warning("encar_healthcheck_no_count keys=%s", list(payload)[:5])
            return False
        total = _to_int_safe(payload.get("Count")) or 0
        # Count без ключа SearchResults — валидный «только счётчик».
        if "SearchResults" not in payload:
            return True
        items = payload.get("SearchResults") or []
        # Явный пустой список при Count > 0 — сломанный контракт.
        if total > 0 and not items:
            logger.warning(
                "encar_healthcheck_broken_contract: count=%d, items=0", total
            )
            return False
        return True

    # --- helpers -------------------------------------------------------

    @staticmethod
    def _parse_cursor(cursor: str | None) -> int:
        """Валидирует курсор и возвращает номер страницы (>= 0).

        Args:
            cursor: Строка курсора или `None` (первая страница, page=0).

        Returns:
            Номер страницы (0-based).

        Raises:
            FetchError: Если курсор не целое неотрицательное число.
        """
        if not cursor:
            return 0
        try:
            page = int(cursor)
        except (TypeError, ValueError) as exc:
            raise FetchError(f"encar: invalid cursor {cursor!r}") from exc
        if page < 0:
            raise FetchError(f"encar: cursor must be >= 0, got {page}")
        return page

    def _build_params(self, q: str, page: int) -> dict[str, Any]:
        """Параметры поиска.

        Контракт (окт 2026): объявления приходят только если передан `sr`
        ("|<sort>|<offset>|<limit>") + `inav=|Metadata|Sort` + пустой `cursor`.
        Без `sr` — только `{"Count": N}` без SearchResults.

        Args:
            q: Строка q-фильтра.
            page: Номер страницы (0-based).

        Returns:
            Словарь query-параметров.
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

        Важно: если `make`/`model` не найдены в словарях, фильтр **НЕ
        отбрасывается молча** — логируется warning и возвращается
        `FetchError`. Иначе API вернёт весь каталог, и пользователь получит
        нерелевантную выдачу под видом отфильтрованной.

        Args:
            c: Критерии поиска.

        Returns:
            Строка q-фильтра.

        Raises:
            FetchError: Если запрошена марка/модель, отсутствующая в
                соответствующем словаре.
        """
        conditions = ["Hidden.N"]

        make = normalize_make(c.make) if c.make else None
        if make:
            kr_make = ENCAR_MAKE_NAMES.get(make)
            if kr_make is None:
                raise FetchError(
                    f"encar: manufacturer {make!r} is not supported by the API"
                )
            conditions.append(f"Manufacturer.{kr_make}")
        if c.model:
            kr_model = ENCAR_MODEL_GROUPS.get(c.model.strip().lower())
            if kr_model is None:
                raise FetchError(
                    f"encar: model {c.model!r} is not supported by the API"
                )
            conditions.append(f"ModelGroup.{kr_model}")

        if c.price_min is not None or c.price_max is not None:
            lo, hi = self._price_range_man(c)
            conditions.append(f"Price.range({lo}..{hi})")

        if c.year_from is not None or c.year_to is not None:
            year_from = _to_int_safe(c.year_from)
            year_to = _to_int_safe(c.year_to)
            if year_from is not None and year_to is not None and year_from > year_to:
                raise FetchError(
                    f"encar: year_from ({year_from}) > year_to ({year_to})"
                )
            y_lo = year_from * 100 + 1 if year_from is not None else 190001
            y_hi = year_to * 100 + 12 if year_to is not None else 209912
            conditions.append(f"Year.range({y_lo}..{y_hi})")

        if c.mileage_max is not None:
            mileage_max = _to_int_safe(c.mileage_max)
            if mileage_max is not None and mileage_max > 0:
                conditions.append(f"Mileage.range(0..{mileage_max})")

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
        USD — конвертация по `config.usd_krw`. Неизвестная валюта —
        `FetchError` (а не тихая трактовка как KRW).

        Для `price_max` используется `round()` (а не truncation), чтобы
        не отсекать объявления чуть ниже максимума.

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
                f"encar: currency {currency!r} is not supported "
                f"(expected one of {sorted(_SUPPORTED_CURRENCIES)})"
            )
        rate = Decimal(self._usd_krw) if currency == "USD" else Decimal(1)

        if c.price_min is not None and c.price_max is not None and c.price_min > c.price_max:
            raise FetchError(
                f"encar: price_min ({c.price_min}) > price_max ({c.price_max})"
            )

        def to_man(value: Decimal) -> int:
            return int((value * rate / Decimal(10_000)).to_integral_value())

        lo = to_man(Decimal(str(c.price_min))) if c.price_min is not None else 0
        hi = to_man(Decimal(str(c.price_max))) if c.price_max is not None else 99_999_999
        return max(lo, 0), max(hi, 0)

    # --- обогащение детальными карточками -------------------------------

    async def _fetch_optional(self, url: str) -> dict[str, Any] | None:
        """GET JSON без фатальных ошибок: 404 «нет данных» → `None`.

        Args:
            url: Абсолютный URL.

        Returns:
            Dict-ответ или `None` при любой ошибке/не-dict.
        """
        try:
            data = await self.http.get_json(url)
        except Exception as exc:  # 404/таймаут/блокировка — не критично
            logger.debug("encar_optional_failed url=%s: %s", url, exc)
            return None
        return data if isinstance(data, dict) else None

    async def _enrich_from_details(self, items: list[UnifiedListing]) -> None:
        """Догрузить детальные данные для каждого объявления.

        Search-выдача не содержит VIN и показывает часть фото. Анонимно
        (окт 2026) работают: `/v1/readside/vehicle/{id}` (VIN, spec, фото,
        флаги), `clean-encar` (безаварийность), `inspection` (чек
        성능점검기록부: агрегаты/зоны/фото), `record/summary` (страховая
        история), `diagnosis` (엔ка진단: зоны/вердикт). Ошибки отдельных
        карточек и отдельных отчётов логируются и не роняют страницу.

        Args:
            items: Объявления страницы (мутируются in-place).
        """
        semaphore = asyncio.Semaphore(_DETAIL_CONCURRENCY)

        async def enrich_one(item: UnifiedListing) -> None:
            async with semaphore:
                try:
                    detail = await self.http.get_json(
                        self._detail_api.format(source_id=item.source_id)
                    )
                    cleaned: bool | None = None
                    try:
                        clean = await self.http.get_json(
                            self._clean_api.format(source_id=item.source_id)
                        )
                        if isinstance(clean, dict):
                            cleaned = bool(clean.get("cleaned"))
                    except Exception as exc:  # флаг безаварийности не критичен
                        logger.debug(
                            "encar_clean_failed id=%s: %s", item.source_id, exc
                        )
                except Exception as exc:
                    logger.warning(
                        "encar_detail_failed id=%s: %s", item.source_id, exc
                    )
                    return
                if not isinstance(detail, dict):
                    return
                sid = item.source_id
                inspection = await self._fetch_optional(
                    self._inspection_api.format(source_id=sid)
                )
                insp_summary = (
                    await self._fetch_optional(
                        self._inspection_summary_api.format(source_id=sid)
                    )
                    if inspection is not None
                    else None
                )
                record = await self._fetch_optional(
                    self._record_api.format(source_id=sid)
                )
                diagnosis = await self._fetch_optional(
                    self._diagnosis_api.format(source_id=sid)
                )
                self._apply_detail(
                    item,
                    detail,
                    cleaned,
                    inspection=inspection,
                    insp_summary=insp_summary,
                    record=record,
                    diagnosis=diagnosis,
                )

        await asyncio.gather(*(enrich_one(item) for item in items))

    def _apply_detail(
        self,
        item: UnifiedListing,
        detail: dict[str, Any],
        cleaned: bool | None,
        inspection: dict[str, Any] | None = None,
        insp_summary: dict[str, Any] | None = None,
        record: dict[str, Any] | None = None,
        diagnosis: dict[str, Any] | None = None,
    ) -> None:
        """Наложить данные детальной карточки на объявление (in-place).

        Args:
            item: Объявление из search-выдачи.
            detail: JSON ответа `/v1/readside/vehicle/{id}`.
            cleaned: Флаг Clean Encar (`None` — не удалось получить).
            inspection: JSON чека осмотра (`None` — нет/ошибка).
            insp_summary: JSON summary чека (`None` — нет/ошибка).
            record: JSON страховой истории (`None` — нет/ошибка).
            diagnosis: JSON 엔ка진단 (`None` — нет/ошибка).
        """
        vin_raw = detail.get("vin")
        vin = vin_raw.strip().upper() if isinstance(vin_raw, str) else ""
        if _VIN_RE.match(vin):
            item.vin = vin
        elif vin:
            logger.warning(
                "encar_bad_vin id=%s: %r — оставляю search-значение",
                item.source_id,
                vin_raw,
            )

        spec = detail.get("spec") if isinstance(detail.get("spec"), dict) else {}
        displacement = spec.get("displacement")
        if isinstance(displacement, (int, float)) and displacement > 0:
            # см³; normalize_engine_volume в writer'е приводит к литрам.
            item.engine_volume = float(displacement)

        # Полный набор фото (OUTER+INNER): search отдаёт лишь часть.
        photos = detail.get("photos")
        if isinstance(photos, list) and photos:
            urls = [
                f"{self._photo_prefix}/{str(p.get('path', '')).lstrip('/')}"
                for p in photos
                if isinstance(p, dict) and p.get("path")
            ]
            if urls:
                item.images = urls

        # Комплектация (миграция 0023): коды опций → RU-названия.
        item.options = self._extract_options(detail)

        # Фото из чека (днище/салон и т.п.) — в отчёт осмотра, не в галерею.
        insp_images = inspection.get("images") if isinstance(inspection, dict) else None
        check_photos: list[str] = []
        if isinstance(insp_images, list) and insp_images:
            for p in insp_images[:20]:  # лимит InspectionReportIn.images
                path = p.get("path") or p.get("location") if isinstance(p, dict) else p
                if isinstance(path, str) and path.strip():
                    path = path.strip()
                    check_photos.append(
                        path if path.startswith("http") else
                        f"{self._photo_prefix}/{path.lstrip('/')}"
                    )

        item.inspection = self._build_inspection(
            detail, cleaned,
            inspection=inspection, insp_summary=insp_summary,
            record=record, diagnosis=diagnosis,
            photos=check_photos,
        )

    @staticmethod
    def _extract_options(detail: dict[str, Any]) -> list[str]:
        """Комплектация из detail.options (коды) → русифицированные названия.

        Encar отдаёт `options.standard` (базовые) и `options.tuning` (тюнинг)
        списками кодов; `choice` (1xxx) — без анонимного словаря, пропускаем.
        Неизвестные коды молча пропускаем: сырые коды на фронте не показываем.

        Args:
            detail: JSON ответа `/v1/readside/vehicle/{id}`.

        Returns:
            Русифицированные названия опций (порядок источника, дубли сняты).
        """
        options_block = (
            detail.get("options") if isinstance(detail.get("options"), dict) else {}
        )
        names: list[str] = []

        def _map(codes: object, table: dict[str, str]) -> None:
            if not isinstance(codes, list):
                return
            for code in codes:
                name = table.get(str(code))
                if name and name not in names:
                    names.append(name)

        _map(options_block.get("standard"), _STANDARD_OPTION_RU)
        _map(options_block.get("tuning"), _TUNING_OPTION_RU)
        return names[:60]  # разумный предел на карточку

    @staticmethod
    def _build_inspection(
        detail: dict[str, Any],
        cleaned: bool | None,
        inspection: dict[str, Any] | None = None,
        insp_summary: dict[str, Any] | None = None,
        record: dict[str, Any] | None = None,
        diagnosis: dict[str, Any] | None = None,
        photos: list[str] | None = None,
    ) -> dict[str, Any]:
        """Собрать отчёт осмотра из анонимных API Encar.

        Источники (все анонимны, окт 2026; `None` — 404/ошибка):
        - detail (`/v1/readside/vehicle/{id}`): флаги карточки;
        - inspection (`.../inspection/vehicle/{id}`): чек 성능점검기록부 —
          флаги ДТП/ремонта (master), чек-лист агрегатов (inners → items),
          зоны кузова (outers → marks);
        - insp_summary (`.../summary`): центр и инспектор → inspector;
        - record (`.../record/vehicle/{id}/summary`): страховая история
          (ДТП, гибель, угоны, владельцы, залог) → facts;
        - diagnosis (`.../diagnosis/vehicle/{id}`): 엔카진단 — зоны
          NORMAL/дефект → marks, вердикт CHECKER_COMMENT → facts.

        grade не выдумываем (остаётся `None`) — Encar онлайн-оценку не
        отдаёт; ручную оценку админ ставит через PUT /listings/{id}/inspection.

        Args:
            detail: JSON детальной карточки.
            cleaned: Флаг Clean Encar (`None` — неизвестен).
            inspection: JSON чека осмотра.
            insp_summary: JSON summary чека.
            record: JSON страховой истории.
            diagnosis: JSON 엔ка진단.
            photos: URL-ы фото чека (днище/салон) — в отчёт, не в галерею.

        Returns:
            Словарь для `inspection_reports` (формат InspectionReport).
        """
        advertisement = (
            detail.get("advertisement")
            if isinstance(detail.get("advertisement"), dict)
            else {}
        )
        condition = (
            detail.get("condition") if isinstance(detail.get("condition"), dict) else {}
        )
        seizing_block = (
            condition.get("seizing") if isinstance(condition.get("seizing"), dict) else {}
        )
        inspection_block = (
            condition.get("inspection")
            if isinstance(condition.get("inspection"), dict)
            else {}
        )
        view_block = detail.get("view") if isinstance(detail.get("view"), dict) else {}
        verification = (
            advertisement.get("verification")
            if isinstance(advertisement.get("verification"), dict)
            else {}
        )

        diagnosis_done = bool(
            advertisement.get("diagnosisCar") or view_block.get("encarDiagnosis")
        )
        record_formats = inspection_block.get("formats") or []
        seizing_count = seizing_block.get("seizingCount") or 0
        pledge_count = seizing_block.get("pledgeCount") or 0

        facts: list[str] = [
            "Диагностика Encar (엔카진단): пройдена"
            if diagnosis_done
            else "Диагностика Encar: не проводилась"
        ]
        if "TABLE" in record_formats:
            facts.append("запись техосмотра (성능기록부): доступна у источника")
        if cleaned is True:
            facts.append("Clean Encar: безаварийная")
        elif cleaned is False:
            facts.append("Clean Encar: статус не подтверждён")
        if seizing_count or pledge_count:
            facts.append(f"обременения: залог={seizing_count}, пледж={pledge_count}")
        if bool(verification.get("reportExists")):
            facts.append("отчёт верификации Encar: есть")
        if bool(advertisement.get("directInspected")):
            facts.append("осмотрена напрямую Encar: да")
        if bool(advertisement.get("hasUnderBodyPhoto")):
            facts.append("фото днища: есть")
        one_line = advertisement.get("oneLineText")
        if isinstance(one_line, str) and one_line.strip():
            facts.append(f"примечание продавца: {one_line.strip()}")

        marks: list[dict[str, Any]] = []
        items: list[dict[str, Any]] = []
        inspector: str | None = None
        inspected_at: str | None = None

        # --- чек осмотра (성능점검기록부) -----------------------------------
        if inspection is not None:
            master = (
                inspection.get("master")
                if isinstance(inspection.get("master"), dict)
                else {}
            )
            m_detail = (
                master.get("detail")
                if isinstance(master.get("detail"), dict)
                else {}
            )
            if master.get("accdient") is False:
                facts.append("чек осмотра: ДТП не отмечены")
            elif master.get("accdient") is True:
                facts.append("чек осмотра: отмечены ДТП")
            if bool(master.get("simpleRepair")):
                facts.append("чек осмотра: простой ремонт (полировка)")
            first_reg = str(m_detail.get("firstRegistrationDate") or "")
            if first_reg:
                facts.append(f"первая регистрация: {first_reg}")
            guaranty = (
                m_detail.get("guarantyType")
                if isinstance(m_detail.get("guarantyType"), dict)
                else {}
            )
            if str(guaranty.get("title") or "") == "보험사보증":
                facts.append("гарантия страховой компании")
            source = (
                inspection.get("inspectionSource")
                if isinstance(inspection.get("inspectionSource"), dict)
                else {}
            )
            if str(source.get("code") or "") == "DIRECT_INPUT":
                facts.append("чек заполнен продавцом/дилером, не сервисом Encar")
            reg_date = str(master.get("registrationDate") or "")
            if reg_date:
                inspected_at = reg_date[:10]

        # --- outers → marks (зоны кузова чека: 교환/판금/도색/…) -------------
        if inspection is not None:
            for outer in inspection.get("outers") or []:
                if not isinstance(outer, dict):
                    continue
                o_type = outer.get("type")
                o_title = o_type.get("title") if isinstance(o_type, dict) else None
                zone = _outer_zone(o_title)
                if zone is None:
                    continue
                st = outer.get("statusType")
                st_title = st.get("title") if isinstance(st, dict) else None
                if not st_title or st_title in ("없음", "정상"):
                    continue
                marks.append(
                    {
                        "zone": zone,
                        "type": _mark_type_from_kr(st_title),
                        "note": str(st_title)[:300],
                    }
                )

            # inners → items (чек-лист агрегатов; берём худший статус группы)
            grouped: dict[str, dict[str, Any]] = {}

            def _walk(node: dict[str, Any], cell: dict[str, Any]) -> None:
                st = _status_of(node.get("statusType"))
                children = [
                    ch for ch in (node.get("children") or []) if isinstance(ch, dict)
                ]
                if st is None:
                    for ch in children:
                        _walk(ch, cell)
                    return
                status, label = st
                if _ITEM_WORST[status] > _ITEM_WORST[cell["status"]]:
                    cell["status"] = status
                if status != "ok":
                    n_type = node.get("type")
                    n_title = n_type.get("title") if isinstance(n_type, dict) else ""
                    cell["notes"].append(f"{n_title} — {label}")

            for group in inspection.get("inners") or []:
                if not isinstance(group, dict):
                    continue
                g_type = group.get("type")
                g_code = (
                    str(g_type.get("code") or "") if isinstance(g_type, dict) else ""
                )
                key = _ITEM_GROUP_KEYS.get(g_code)
                if key is None:
                    continue
                cell = grouped.setdefault(key, {"status": "ok", "notes": []})
                for child in group.get("children") or []:
                    if isinstance(child, dict):
                        _walk(child, cell)

            for key in _ITEM_ORDER:
                cell = grouped.get(key)
                if cell is None:
                    continue
                entry: dict[str, Any] = {"key": key, "status": cell["status"]}
                if cell["notes"]:
                    entry["note"] = "; ".join(cell["notes"])[:300]
                items.append(entry)

            insp_name = insp_summary.get("inspName") if isinstance(
                insp_summary, dict
            ) else None
            if isinstance(insp_name, str) and insp_name.strip():
                inspector = insp_name.strip()[:120]

        # --- страховая история (보험이력) ------------------------------------
        if record is not None:

            def _cnt(key: str) -> int:
                value = record.get(key)
                return value if isinstance(value, int) else 0

            my = _cnt("myAccidentCnt")
            other = _cnt("otherAccidentCnt")
            if my or other:
                facts.append(
                    f"страховые случаи: {my + other} "
                    f"(этим авто: {my}, у других: {other})"
                )
            if _cnt("floodTotalLossCnt"):
                facts.append(
                    f"полная гибель (наводнение): {_cnt('floodTotalLossCnt')}"
                )
            if _cnt("floodPartLossCnt"):
                facts.append(
                    f"частичная гибель (наводнение): {_cnt('floodPartLossCnt')}"
                )
            if _cnt("robberCnt"):
                facts.append(f"угонов: {_cnt('robberCnt')}")
            if _cnt("ownerChangeCnt"):
                facts.append(f"смен владельцев: {_cnt('ownerChangeCnt')}")
            if _cnt("carNoChangeCnt"):
                facts.append(f"смен госномера: {_cnt('carNoChangeCnt')}")
            if record.get("loan"):
                facts.append("залог/кредит в истории")
            if record.get("business"):
                facts.append("служебное использование в истории")
            if record.get("government"):
                facts.append("использование госорганами в истории")
            if not any(f.startswith("первая регистрация") for f in facts):
                first_date = str(record.get("firstDate") or "")
                if first_date:
                    facts.append(f"первая регистрация: {first_date}")

        # --- 엔ка진단 (диагностика Encar) ------------------------------------
        if diagnosis is not None:
            comments: list[str] = []
            for d_item in diagnosis.get("items") or []:
                if not isinstance(d_item, dict):
                    continue
                d_name = str(d_item.get("name") or "")
                if d_name in _DIAGNOSIS_COMMENT_KEYS:
                    text = str(d_item.get("result") or "").strip()
                    if text:
                        comments.append(text)
                    continue
                zone = _DIAGNOSIS_ZONES.get(d_name)
                # None — зона вне нашей схемы (REAR_BUMPER) или неизвестна.
                if zone is None:
                    continue
                if str(d_item.get("resultCode") or "").upper() == "NORMAL":
                    continue
                d_result = str(d_item.get("result") or "").strip()
                marks.append(
                    {
                        "zone": zone,
                        "type": _mark_type_from_kr(d_result),
                        "note": (d_result[:300] or None),
                    }
                )
            if comments:
                if any("무사고" in c for c in comments):
                    facts.append("вердикт Encar: 무사고 (без ДТП)")
                else:
                    facts.append(f"вердикт Encar: {' / '.join(comments)[:120]}")
            d_date = str(diagnosis.get("diagnosisDate") or "")[:10]
            d_center = str(diagnosis.get("reservationCenterName") or "").strip()
            d_extra = " ".join(x for x in (d_date, d_center) if x)
            facts[0] = "Диагностика Encar (엔카진단): пройдена" + (
                f" — {d_extra}" if d_extra else ""
            )

        summary = " · ".join(facts)
        if len(summary) > 1000:  # лимит InspectionReportIn/Out
            summary = summary[:997] + "…"

        return {
            "source": "encar",
            "inspector": inspector,
            "inspected_at": inspected_at,
            "grade": None,
            "summary": summary,
            "marks": marks,
            "items": items,
            "images": photos or [],
        }

    def _map_item(self, raw: dict[str, Any]) -> UnifiedListing | None:
        """Преобразует один элемент `SearchResults` в `UnifiedListing`.

        Любая ошибка парсинга поля (price/mileage/year) логируется и
        приводит к `None` для этого объявления — не роняет `fetch`.

        Args:
            raw: Словарь объявления из API.

        Returns:
            `UnifiedListing` или `None`.
        """
        source_id = _to_int_safe(raw.get("Id"))
        if source_id is None:
            return None

        price_man = _to_decimal_safe(raw.get("Price"))
        price = price_man * 10_000 if price_man is not None else None

        # Photos — список dict {'type','location',...}; раньше str(dict) давал
        # мусорные URLs (…/%7B'type':…%7D) и фото не открывались. Разворачиваем
        # location; строковые пути (старый контракт) — как раньше.
        photos: list[str] = []
        raw_photos = raw.get("Photos")
        if not isinstance(raw_photos, list):
            raw_photos = [raw["Photo"]] if raw.get("Photo") else []
        for p in raw_photos:
            path = p.get("location") if isinstance(p, dict) else p
            if path:
                photos.append(f"{self._photo_prefix}/{str(path).lstrip('/')}")

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

        mileage_km = _to_int_safe(raw.get("Mileage"))
        vin_raw = raw.get("Vin")
        vin = vin_raw.strip().upper() if isinstance(vin_raw, str) and vin_raw.strip() else None

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
            mileage_km=mileage_km,
            vin=vin,
            body_type=normalize_body(raw.get("BodyType")) or raw.get("BodyType") or None,
            fuel=normalize_fuel(raw.get("FuelType")),
            transmission=normalize_transmission(raw.get("Transmission")),
            color=normalize_color(raw.get("Color")) or raw.get("Color") or None,
            location=raw.get("OfficeCityState") or raw.get("OfficeCity") or None,
            seller_type="dealer",
            images=photos,
            published_at=raw.get("PhotoDate"),
            raw=raw,
        )

    @staticmethod
    def _extract_year(raw: dict[str, Any]) -> int | None:
        """Год из FormYear ('2022') или Year (202208.0 → 2022).

        Args:
            raw: Словарь объявления из API.

        Returns:
            Год как int или `None`, если не удалось извлечь.
        """
        form_year = raw.get("FormYear")
        if form_year:
            try:
                return int(str(form_year)[:4])
            except ValueError:
                pass
        year_num = _to_int_safe(raw.get("Year"))
        if year_num is None:
            return None
        return year_num // 100 if year_num > 190000 else year_num