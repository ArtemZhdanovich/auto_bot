"""Нормализация значений: марки, топливо, кузов, КПП, привод (ТЗ п. 6.3).

Справочники могут дополняться из БД (`ref_*` таблицы) на следующих этапах.

Все функции принимают «сырое» значение от источника и возвращают
каноническое. Для неизвестных значений поведение явно задокументировано:
`normalize_make`/`normalize_color` возвращают title-case/raw (lenient),
`normalize_fuel`/`normalize_body`/`normalize_transmission`/`normalize_drive`
возвращают `None` (strict) — они участвуют в фильтрах, где мусор хуже
пропуска.
"""

import logging
import math
import re
from typing import Final

logger = logging.getLogger(__name__)

# --- Справочники --------------------------------------------------------

FUEL_MAP = {
    "gasoline": "petrol", "petrol": "petrol", "бензин": "petrol", "휘발유": "petrol",
    "가솔린": "petrol",
    "汽油": "petrol",
    "diesel": "diesel", "дизель": "diesel", "디젤": "diesel",
    "柴油": "diesel",
    "hybrid": "hybrid", "гибрид": "hybrid", "하이브리드": "hybrid",
    "混合动力": "hybrid", "油电混合": "hybrid", "插电混动": "hybrid",
    "electric": "electric", "ev": "electric", "электро": "electric", "전기": "electric",
    "纯电动": "electric", "电动": "electric",
    "lpg": "lpg", "гбо": "lpg", "газ": "lpg", "가스": "lpg",
    "油改气": "lpg",
    "휘발유+전기": "hybrid", "디젤+전기": "hybrid",
}

BODY_MAP = {
    "suv": "suv", "внедорожник": "suv", "rv": "suv",
    "sedan": "sedan", "седан": "sedan", "세단": "sedan",
    # Корейская классификация кузовов (kcar carctgrNm)
    "경차": "hatchback",  # кей-кар
    "소형차": "hatchback",  # малый класс
    "준중형차": "sedan",  # субкомпакт
    "중형차": "sedan",  # средний класс
    "대형차": "sedan",  # полноразмерный
    "승합차": "van",  # микроавтобус
    # 화물차 = грузовик, не пикап. Отдельная категория (см. models.truck).
    "화물차": "truck",
    "hatchback": "hatchback", "хэтчбек": "hatchback",
    "wagon": "wagon", "универсал": "wagon",
    "coupe": "coupe", "купе": "coupe", "쿠페": "coupe",
    "van": "van", "minivan": "van", "минивэн": "van",
    "pickup": "pickup", "пикап": "pickup",
    "truck": "truck", "грузовик": "truck",
    "convertible": "convertible", "кабриолет": "convertible",
}

TRANSMISSION_MAP = {
    "automatic": "automatic", "auto": "automatic", "at": "automatic",
    "автомат": "automatic", "акпп": "automatic", "오토": "automatic",
    "自动": "automatic", "自动挡": "automatic",
    "manual": "manual", "mt": "manual", "механика": "manual", "мкпп": "manual",
    "手动": "manual", "手动挡": "manual",
    "cvt": "cvt", "вариатор": "cvt",
    "无级变速": "cvt",
    "dct": "dct", "робот": "dct",
    "双离合": "dct", "湿式双离合": "dct", "干式双离合": "dct",
}

DRIVE_MAP = {
    "fwd": "fwd", "передний": "fwd", "전륜": "fwd", "전륜구동": "fwd",
    "rwd": "rwd", "задний": "rwd", "후륜": "rwd", "후륜구동": "rwd",
    "awd": "awd", "4wd": "awd", "4x4": "awd", "полный": "awd",
    "4륜": "awd", "사륜": "awd", "사륜구동": "awd",
}

MAKE_MAP = {
    "hyundai": "Hyundai", "현대": "Hyundai",
    "kia": "Kia", "киа": "Kia", "기아": "Kia",
    "genesis": "Genesis", "дженезис": "Genesis", "제네시스": "Genesis",
    "chevrolet": "Chevrolet", "шевроле": "Chevrolet", "쉐보레": "Chevrolet",
    "bmw": "BMW", "бмв": "BMW",
    "mercedes-benz": "Mercedes-Benz", "mercedes": "Mercedes-Benz",
    "мерседес": "Mercedes-Benz",
    "мерседес бенц": "Mercedes-Benz",
    "audi": "Audi", "ауди": "Audi",
    "volkswagen": "Volkswagen", "vw": "Volkswagen", "фольксваген": "Volkswagen",
    "toyota": "Toyota", "тойота": "Toyota",
    "lexus": "Lexus", "лексус": "Lexus",
    "nissan": "Nissan", "ниссан": "Nissan",
    "honda": "Honda", "хонда": "Honda",
    "ford": "Ford", "форд": "Ford",
    "volvo": "Volvo", "вольво": "Volvo",
    "porsche": "Porsche", "порше": "Porsche", "포르쉐": "Porsche",
    # --- Корея: имена из живых API (kcar mnuftrNm / encar Manufacturer) ---
    "벤츠": "Mercedes-Benz",
    "아우디": "Audi",
    "폭스바겐": "Volkswagen",
    "토요타": "Toyota",
    "렉서스": "Lexus",
    "닛산": "Nissan",
    "혼다": "Honda",
    "포드": "Ford",
    "볼보": "Volvo",
    "랜드로버": "Land Rover",
    "재규어": "Jaguar",
    "미니": "MINI",
    "르노": "Renault",
    "푸조": "Peugeot",
    "시트로엥": "Citroen",
    "피아트": "Fiat",
    "지프": "Jeep",
    "캐딜락": "Cadillac",
    "마쯔다": "Mazda",
    "미쯔비시": "Mitsubishi",
    "스즈키": "Suzuki",
    "스바루": "Subaru",
    # --- Английские варианты, которые ранее не матчились и создавали дубли ---
    "land rover": "Land Rover", "landrover": "Land Rover",
    "ленд ровер": "Land Rover", "лендровер": "Land Rover",
    "alfa romeo": "Alfa Romeo", "alfaromeo": "Alfa Romeo",
    "alfa-romeo": "Alfa Romeo", "альфа ромео": "Alfa Romeo",
    "gmc": "GMC",
    "nio": "NIO",
    "zeekr": "ZEEKR",
    "xpeng": "XPeng", "小鹏": "XPeng",
    "byd": "BYD", "chery": "Chery", "cheri": "Chery", "черри": "Chery",
    "geely": "Geely", "джили": "Geely", "haval": "Haval", "хавал": "Haval",
    "mg": "MG", "li-auto": "Li Auto", "li": "Li Auto", "ли": "Li Auto",
    # --- Китай (che168/autohome: иероглифы в SSR-вёрстке) ---
    "丰田": "Toyota",
    "本田": "Honda",
    "日产": "Nissan", "尼桑": "Nissan",
    "马自达": "Mazda",
    "三菱": "Mitsubishi",
    "铃木": "Suzuki",
    "斯巴鲁": "Subaru",
    "现代": "Hyundai",
    "起亚": "Kia",
    "大众": "Volkswagen",
    "奥迪": "Audi",
    "宝马": "BMW",
    "奔驰": "Mercedes-Benz",
    "保时捷": "Porsche",
    "雷克萨斯": "Lexus",
    "沃尔沃": "Volvo",
    "路虎": "Land Rover", "陆虎": "Land Rover",
    "捷豹": "Jaguar",
    "凯迪拉克": "Cadillac",
    "别克": "Buick",
    "雪佛兰": "Chevrolet",
    "福特": "Ford",
    "克莱斯勒": "Chrysler",
    "吉普": "Jeep",
    "迷你": "MINI", "mini": "MINI",
    "菲亚特": "Fiat",
    "标致": "Peugeot",
    "雪铁龙": "Citroen",
    "雷诺": "Renault",
    # Китайские бренды
    "比亚迪": "BYD",
    "吉利": "Geely",
    "奇瑞": "Chery",
    "长城": "Great Wall",
    "哈弗": "Haval", "哈佛": "Haval",
    "坦克": "Tank",
    "魏牌": "Wey",
    "欧拉": "Ora",
    "长安": "Changan",
    "红旗": "Hongqi",
    "荣威": "Roewe",
    "名爵": "MG",
    "五菱": "Wuling",
    "传祺": "Trumpchi", "广汽传祺": "Trumpchi",
    "东风": "Dongfeng",
    "启辰": "Venucia",
    "奔腾": "Bestune",
    "江淮": "JAC",
    "海马": "Haima",
    "理想": "Li Auto",
    "蔚来": "NIO",
    "小鹏": "XPeng",
    "小米": "Xiaomi",
    "零跑": "Leapmotor",
    "哪吒": "Neta",
    "极氪": "Zeekr",
    "岚图": "Voyah",
    "问界": "AITO",
    "深蓝": "Deepal",
    "埃安": "Aion",
}

COLOR_MAP = {
    "black": "black", "черный": "black", "чёрный": "black", "검은색": "black", "검정": "black", "검정색": "black",
    "white": "white", "белый": "white", "흰색": "white", "흰색청": "white",
    "silver": "silver", "серебристый": "silver", "серебряный": "silver", "은색": "silver", "실버": "silver",
    "gray": "gray", "grey": "gray", "серый": "gray", "회색": "gray", "쥐색": "gray",
    "blue": "blue", "синий": "blue", "청색": "blue", "파랑": "blue", "파란색": "blue",
    "red": "red", "красный": "red", "빨간색": "red", "빨강": "red",
    "green": "green", "зеленый": "green", "зелёный": "green", "녹색": "green", "초록": "green",
    "yellow": "yellow", "желтый": "yellow", "жёлтый": "yellow", "노란색": "yellow",
    "brown": "brown", "коричневый": "brown", "갈색": "brown",
    "orange": "orange", "оранжевый": "orange", "주황색": "orange",
    "purple": "purple", "фиолетовый": "purple", "보라색": "purple",
    "beige": "beige", "бежевый": "beige", "베이지": "beige",
    "gold": "gold", "золотой": "gold", "금색": "gold",
    "pink": "pink", "розовый": "pink", "분홍색": "pink",
    "cyan": "cyan", "бирюзовый": "cyan", "청록색": "cyan",
}

# --- Нормализация строк ------------------------------------------------

# Zero-width и прочие невидимые символы, которые `.strip()` не убирает.
_INVISIBLE_RE = re.compile(r"[\u200b-\u200f\u2028-\u202f\u2060\ufeff]")


def _clean(raw: str | None) -> str:
    """Нормализует строку: trim, lower, коллапс пробелов, удаление zero-width.

    Args:
        raw: Сырое значение или `None`.

    Returns:
        Нормализованная строка (пустая для `None`/пустой строки).
    """
    if raw is None:
        return ""
    value = _INVISIBLE_RE.sub("", raw)
    value = " ".join(value.split())  # коллапс любых пробелов, включая nbsp
    return value.lower()


def _make_lookup_key(value: str) -> str:
    """Ключ для fuzzy-lookup марки: без пробелов и дефисов, lower.

    Позволяет `"Mercedes Benz"`, `"mercedes-benz"` и `"MercedesBenz"`
    свести к одному канону.

    Args:
        value: Нормализованное (после `_clean`) значение.

    Returns:
        Строка без пробелов/дефисов в lower-case.
    """
    return re.sub(r"[\s\-]+", "", value.lower())


def _build_lookup(mapping: dict[str, str]) -> dict[str, str]:
    """Строит lookup-индекс по нормализованной форме ключей И значений.

    Значения добавляются, чтобы канонические имена (`"Land Rover"`)
    автоматически регистрировали свои варианты (`"landrover"`).

    Args:
        mapping: Исходный справочник `{raw: canonical}`.

    Returns:
        Индекс `{normalized: canonical}`.
    """
    index: dict[str, str] = {}
    for raw, canonical in mapping.items():
        index[_make_lookup_key(raw)] = canonical
        index[_make_lookup_key(canonical)] = canonical
    return index


_MAKE_LOOKUP: Final = _build_lookup(MAKE_MAP)
_COLOR_LOOKUP: Final = _build_lookup(COLOR_MAP)


# --- Детекция по свободному тексту ------------------------------------

# Ключи, отсортированные по длине desc (предвычислено при импорте).
_FUEL_DETECT: Final = tuple(
    sorted(FUEL_MAP.items(), key=lambda kv: len(kv[0]), reverse=True)
)
_TRANSMISSION_DETECT: Final = tuple(
    sorted(TRANSMISSION_MAP.items(), key=lambda kv: len(kv[0]), reverse=True)
)


def _is_cjk(ch: str) -> bool:
    """`True`, если символ — CJK/хангыль (word boundary для них не работает).

    Args:
        ch: Один символ.

    Returns:
        `True` для китайских/корейских иероглифов.
    """
    return (
        "\u4e00" <= ch <= "\u9fff"  # CJK Unified Ideographs
        or "\uac00" <= ch <= "\ud7af"  # Hangul syllables
    )


def _key_matches(text: str, key: str) -> bool:
    """Проверяет вхождение ключа в текст с учётом письменности.

    Для Latin/Cyrillic — word boundary (`\\b`), чтобы `"ev"` не матчился
    в `"every"`. Для CJK — прямой поиск подстроки (`"自动"` в `"自动挡"`).

    Args:
        text: Нормализованный (lower) текст.
        key: Ключ справочника (уже в lower).

    Returns:
        `True`, если ключ встречается в тексте как отдельное слово/CJK-подстрока.
    """
    if any(_is_cjk(ch) for ch in key):
        return key in text
    return re.search(rf"\b{re.escape(key)}\b", text) is not None


def _detect_from_text(
    text: str | None, mapping: tuple[tuple[str, str], ...]
) -> str | None:
    """Поиск значения справочника как слова в свободном тексте карточки.

    Для SSR-парсинга (che168), где топливо/КПП — кусок текста, а не поле.
    Ключи длиннее проверяются раньше («插电混动» до «电动», «automatic»
    до «at»).

    Args:
        text: Свободный текст карточки.
        mapping: Предвычисленный кортеж `(key, value)`, отсортированный
            по длине ключа desc.

    Returns:
        Каноническое значение или `None`.
    """
    if not text:
        return None
    normalized = text.lower()
    for key, value in mapping:
        if _key_matches(normalized, key):
            return value
    return None


# --- Публичные функции -------------------------------------------------


def normalize_make(raw: str | None) -> str | None:
    """Hyundai / HYUNDAI / 현대 / Mercedes-Benz / Mercedes Benz → канон.

    Lenient: неизвестное значение возвращается в title-case (для
    совместимости). Если нужна строгая валидация — используйте
    `normalize_make(..., strict=True)` (см. ниже).

    Args:
        raw: Сырое значение марки.

    Returns:
        Каноническое имя марки, title-case неизвестного или `None`.
    """
    value = _clean(raw)
    if not value:
        return None
    canonical = _MAKE_LOOKUP.get(_make_lookup_key(value))
    if canonical:
        return canonical
    logger.info("unknown make kept as-is: %r", raw)
    return value.title()


def normalize_fuel(raw: str | None) -> str | None:
    """gasoline / бензин / 휘발유 → 'petrol'.

    Args:
        raw: Сырое значение топлива.

    Returns:
        Канонический код или `None` (strict — для фильтров).
    """
    return FUEL_MAP.get(_clean(raw))


def normalize_body(raw: str | None) -> str | None:
    """suv / внедорожник / SUV → 'suv'.

    Args:
        raw: Сырое значение кузова.

    Returns:
        Канонический код или `None` (strict).
    """
    return BODY_MAP.get(_clean(raw))


def normalize_transmission(raw: str | None) -> str | None:
    """automatic / автомат / 오토 / 自动 → 'automatic'.

    Args:
        raw: Сырое значение КПП.

    Returns:
        Канонический код или `None` (strict).
    """
    return TRANSMISSION_MAP.get(_clean(raw))


def detect_fuel(text: str | None) -> str | None:
    """«5.2万公里 汽油 自动挡» → 'petrol' (детекция по тексту карточки).

    Args:
        text: Свободный текст карточки.

    Returns:
        Канонический код топлива или `None`.
    """
    return _detect_from_text(text, _FUEL_DETECT)


def detect_transmission(text: str | None) -> str | None:
    """«…自动挡…» → 'automatic' (детекция по тексту карточки).

    Args:
        text: Свободный текст карточки.

    Returns:
        Канонический код КПП или `None`.
    """
    return _detect_from_text(text, _TRANSMISSION_DETECT)


def normalize_drive(raw: str | None) -> str | None:
    """awd / 4wd / полный → 'awd'.

    Args:
        raw: Сырое значение привода.

    Returns:
        Канонический код или `None` (strict).
    """
    return DRIVE_MAP.get(_clean(raw))


def normalize_seller_type(raw: str | None) -> str | None:
    """private / частник / dealer / дилер → 'private' | 'dealer' | 'auction'.

    Args:
        raw: Сырое значение типа продавца.

    Returns:
        Канонический код или `None`.
    """
    value = _clean(raw)
    if not value:
        return None
    if value in {"private", "частник", "частное лицо", "физлицо"}:
        return "private"
    if value in {"dealer", "дилер", "салон", "автосалон"}:
        return "dealer"
    if value in {"auction", "аукцион", "경매"}:
        return "auction"
    return None


def normalize_color(raw: str | None) -> str | None:
    """청색 / синий / blue → 'blue'.

    Lenient: неизвестный цвет возвращается нормализованным (lower, коллапс
    пробелов), но не переводится в title-case — это позволяет отделить
    «названия краски» (`"candy blue"`) от базовых цветов.

    Args:
        raw: Сырое значение цвета.

    Returns:
        Канонический код, нормализованное значение или `None`.
    """
    value = _clean(raw)
    if not value:
        return None
    canonical = _COLOR_LOOKUP.get(_make_lookup_key(value))
    if canonical:
        return canonical
    logger.info("unknown color kept as-is: %r", raw)
    return value


# Единицы объёма двигателя для strip перед `float()`.
_ENGINE_UNITS_RE = re.compile(
    r"(?i)\s*(cc|см³|см3|л|l|liter|litre|литр[а-я]*)\s*$"
)


def normalize_engine_volume(
    displacement_cc: float | str | int | None,
) -> float | None:
    """1995 (см³) / '1.6L' / 2.0 (литры) → 2.0 (литры, один знак).

    Heuristic: значения > 100 трактуются как см³ и делятся на 1000.
    Порог выбран под легковые авто (600–8000 см³). Скутеры/мотоциклы
    (50–125 см³) не поддерживаются — задокументировать при необходимости.

    Args:
        displacement_cc: Объём в см³, литрах или строке с суффиксом
            (`"1.6L"`, `"1598cc"`, `"1,6"`).

    Returns:
        Объём в литрах с одним знаком после запятой или `None`.
    """
    if displacement_cc is None or displacement_cc == "":
        return None
    s = str(displacement_cc).strip().replace(",", ".")
    s = _ENGINE_UNITS_RE.sub("", s)
    try:
        value = float(s)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value) or value <= 0:
        return None
    if value > 100:  # см³ → литры
        value = value / 1000.0
    return round(value, 1)