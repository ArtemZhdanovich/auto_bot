"""Нормализация значений: марки, топливо, кузов, КПП, привод (ТЗ п. 6.3).

Справочники могут дополняться из БД (`ref_*` таблицы) на следующих этапах.
"""

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
    "화물차": "pickup",  # грузовой
    "hatchback": "hatchback", "хэтчбек": "hatchback",
    "wagon": "wagon", "универсал": "wagon",
    "coupe": "coupe", "купе": "coupe", "쿠페": "coupe",
    "van": "van", "minivan": "van", "минивэн": "van",
    "pickup": "pickup", "пикап": "pickup",
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
    "fwd": "fwd", "передний": "fwd",
    "rwd": "rwd", "задний": "rwd",
    "awd": "awd", "4wd": "awd", "4x4": "awd", "полный": "awd",
}

MAKE_MAP = {
    "hyundai": "Hyundai", "현대": "Hyundai",
    "kia": "Kia", "киа": "Kia", "기아": "Kia",
    "genesis": "Genesis", "дженезис": "Genesis", "제네시스": "Genesis",
    "chevrolet": "Chevrolet", "шевроле": "Chevrolet", "쉐보레": "Chevrolet",
    "bmw": "BMW", "бмв": "BMW",
    "mercedes-benz": "Mercedes-Benz", "mercedes": "Mercedes-Benz",
    "мерседес": "Mercedes-Benz",
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


def _clean(raw: str | None) -> str:
    return (raw or "").strip().lower()


def normalize_make(raw: str | None) -> str | None:
    """Hyundai / HYUNDAI / 현대 → 'Hyundai'."""
    value = _clean(raw)
    if not value:
        return None
    return MAKE_MAP.get(value) or value.title()


def normalize_fuel(raw: str | None) -> str | None:
    """gasoline / бензин / 휘발유 → 'petrol'."""
    return FUEL_MAP.get(_clean(raw))


def normalize_body(raw: str | None) -> str | None:
    """suv / внедорожник / SUV → 'suv'."""
    return BODY_MAP.get(_clean(raw))


def normalize_transmission(raw: str | None) -> str | None:
    """automatic / автомат / 오토 / 自动 → 'automatic'."""
    return TRANSMISSION_MAP.get(_clean(raw))


def _detect_from_text(text: str | None, mapping: dict[str, str]) -> str | None:
    """Поиск значения справочника как подстроки в свободном тексте карточки.

    Для SSR-парсинга (che168), где топливо/КПП — кусок текста, а не поле.
    Ключи длиннее проверяются раньше («插电混动» до «电动»).
    """
    if not text:
        return None
    for key, value in sorted(mapping.items(), key=lambda kv: len(kv[0]), reverse=True):
        if len(key) > 1 and key in text:
            return value
    return None


def detect_fuel(text: str | None) -> str | None:
    """«5.2万公里 汽油 自动挡» → 'petrol' (подстрока в тексте карточки)."""
    return _detect_from_text(text, FUEL_MAP)


def detect_transmission(text: str | None) -> str | None:
    """«…自动挡…» → 'automatic' (подстрока в тексте карточки)."""
    return _detect_from_text(text, TRANSMISSION_MAP)


def normalize_drive(raw: str | None) -> str | None:
    """awd / 4wd / полный → 'awd'."""
    return DRIVE_MAP.get(_clean(raw))


def normalize_seller_type(raw: str | None) -> str | None:
    """private / частник → 'private'."""
    value = _clean(raw)
    if not value:
        return None
    if value in {"private", "частник", "частное лицо"}:
        return "private"
    if value in {"dealer", "дилер"}:
        return "dealer"
    if value in {"auction", "аукцион", "경매"}:
        return "auction"
    return None


# Цвета: encar detail (spec.colorName — корейский) + рус/англ (детализация
# фильтра «цвет», как на avtokorea.by). Канон — англ., в UI русские подписи.
COLOR_MAP = {
    "black": "black", "черный": "black", "чёрный": "black", "검은색": "black", "검정": "black", "검정색": "black",
    "white": "white", "белый": "white", "흰색": "white", "흰색청": "white",
    "silver": "silver", "серебристый": "silver", "은색": "silver", "실버": "silver",
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


def normalize_color(raw: str | None) -> str | None:
    """청색 / синий / blue → 'blue'; нераспознанное — как есть."""
    value = _clean(raw)
    if not value:
        return None
    return COLOR_MAP.get(value) or value


def normalize_engine_volume(displacement_cc: float | str | int | None) -> float | None:
    """1995 (см³) / '1995' / 2.0 (уже литры) → 2.0 (литры, один знак)."""
    if displacement_cc is None or displacement_cc == "":
        return None
    try:
        value = float(displacement_cc)
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    if value > 50:  # см³ → литры
        value = value / 1000.0
    return round(value, 1)
