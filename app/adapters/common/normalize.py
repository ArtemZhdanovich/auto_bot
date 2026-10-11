"""Нормализация значений: марки, топливо, кузов, КПП, привод (ТЗ п. 6.3).

Справочники могут дополняться из БД (`ref_*` таблицы) на следующих этапах.
"""

FUEL_MAP = {
    "gasoline": "petrol", "petrol": "petrol", "бензин": "petrol", "휘발유": "petrol",
    "가솔린": "petrol",
    "diesel": "diesel", "дизель": "diesel", "디젤": "diesel",
    "hybrid": "hybrid", "гибрид": "hybrid", "하이브리드": "hybrid",
    "electric": "electric", "ev": "electric", "электро": "electric", "전기": "electric",
    "lpg": "lpg", "гбо": "lpg", "газ": "lpg", "가스": "lpg",
    "휘발유+전기": "hybrid", "디젤+전기": "hybrid",
}

BODY_MAP = {
    "suv": "suv", "внедорожник": "suv", "rv": "suv",
    "sedan": "sedan", "седан": "sedan", "세단": "sedan",
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
    "manual": "manual", "mt": "manual", "механика": "manual", "мкпп": "manual",
    "cvt": "cvt", "вариатор": "cvt",
    "dct": "dct", "робот": "dct",
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
    "porsche": "Porsche", "порше": "Porsche",
    "byd": "BYD", "chery": "Chery", "cheri": "Chery", "черри": "Chery",
    "geely": "Geely", "джили": "Geely", "haval": "Haval", "хавал": "Haval",
    "mg": "MG", "li-auto": "Li Auto", "li": "Li Auto", "ли": "Li Auto",
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
    """automatic / автомат / 오토 → 'automatic'."""
    return TRANSMISSION_MAP.get(_clean(raw))


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
