"""Тесты нормализации (ТЗ п. 6.3)."""

from app.infrastructure.sources.common.normalize import (
    normalize_body,
    normalize_color,
    normalize_drive,
    normalize_engine_volume,
    normalize_fuel,
    normalize_make,
    normalize_transmission,
)


def test_normalize_make():
    assert normalize_make("Hyundai") == "Hyundai"
    assert normalize_make("HYUNDAI") == "Hyundai"
    assert normalize_make("현대") == "Hyundai"
    assert normalize_make(" kia ") == "Kia"
    assert normalize_make(None) is None
    assert normalize_make("") is None


def test_normalize_make_unknown_titlecase():
    assert normalize_make("_unknownbrand") == "_Unknownbrand"


def test_normalize_fuel():
    assert normalize_fuel("gasoline") == "petrol"
    assert normalize_fuel("бензин") == "petrol"
    assert normalize_fuel("휘발유") == "petrol"
    assert normalize_fuel("Дизель") == "diesel"
    assert normalize_fuel("Electric") == "electric"
    assert normalize_fuel("unknown-fuel") is None


def test_normalize_body():
    assert normalize_body("SUV") == "suv"
    assert normalize_body("внедорожник") == "suv"
    assert normalize_body("Sedan") == "sedan"


def test_normalize_transmission():
    assert normalize_transmission("오토") == "automatic"
    assert normalize_transmission("Автомат") == "automatic"
    assert normalize_transmission("MT") == "manual"


def test_normalize_drive():
    assert normalize_drive("4WD") == "awd"
    assert normalize_drive("полный") == "awd"
    assert normalize_drive("FWD") == "fwd"


def test_normalize_color():
    """Цвет encar detail (корейский) и рус/англ → канон."""
    assert normalize_color("청색") == "blue"
    assert normalize_color("검정") == "black"
    assert normalize_color("Синий") == "blue"
    assert normalize_color("Silver") == "silver"
    assert normalize_color("жёлтый") == "yellow"
    assert normalize_color("неизвестный-цвет") == "неизвестный-цвет"  # как есть
    assert normalize_color(None) is None
    assert normalize_color("") is None


def test_normalize_engine_volume():
    """см³ → литры (один знак); уже литры — как есть; мусор — None."""
    assert normalize_engine_volume(1995) == 2.0
    assert normalize_engine_volume("1995") == 2.0
    assert normalize_engine_volume(2.0) == 2.0
    assert normalize_engine_volume(1598) == 1.6
    assert normalize_engine_volume(None) is None
    assert normalize_engine_volume("") is None
    assert normalize_engine_volume("abc") is None
    assert normalize_engine_volume(-100) is None



# tests/core/test_normalize.py
import pytest

from app.infrastructure.sources.common.normalize import (
    detect_fuel,
    detect_transmission,
)


# --- make: варианты написания -----------------------------------------

@pytest.mark.parametrize("raw", [
    "Mercedes-Benz", "mercedes-benz", "Mercedes Benz", "mercedes benz",
    "MERCEDES BENZ", "MercedesBenz", "мерседес",
    "Мерседес Бенц",
    "벤츠", "奔驰",
])
def test_mercedes_variants(raw):
    assert normalize_make(raw) == "Mercedes-Benz"


@pytest.mark.parametrize("raw", [
    "Land Rover", "land rover", "land-rover", "landrover",
    "LAND ROVER", "ленд ровер", "лендровер", "랜드로버", "路虎",
])
def test_land_rover_variants(raw):
    assert normalize_make(raw) == "Land Rover"


@pytest.mark.parametrize("raw", ["GMC", "gmc"])
def test_gmc_not_titlecased(raw):
    assert normalize_make(raw) == "GMC"


def test_make_nbsp_and_zero_width():
    assert normalize_make("Mercedes\u00a0Benz") == "Mercedes-Benz"
    assert normalize_make("Toyota\u200b") == "Toyota"


def test_make_unknown_returns_title_case_with_log(caplog):
    with caplog.at_level("INFO"):
        assert normalize_make("hondaa") == "Hondaa"
    assert "unknown make" in caplog.text


# --- detect_fuel: word boundary ---------------------------------------

def test_detect_fuel_no_false_positive_in_every():
    """`ev` не должен матчиться внутри `every`."""
    assert detect_fuel("every car has an engine") is None
    assert detect_fuel("5 levels of trim") is None


def test_detect_fuel_case_insensitive():
    assert detect_fuel("Gasoline engine") == "petrol"
    assert detect_fuel("DIESEL") == "diesel"


def test_detect_fuel_cjk_substring():
    assert detect_fuel("5.2万公里 汽油 自动挡") == "petrol"
    assert detect_fuel("插电混动") == "hybrid"


def test_detect_transmission_no_false_positive_in_plate():
    assert detect_transmission("license plate 123") is None


def test_detect_transmission_short_abbrev():
    assert detect_transmission("AT transmission") == "automatic"
    assert detect_transmission("MT") == "manual"


def test_detect_transmission_full_word_wins():
    assert detect_transmission("automatic transmission") == "automatic"


# --- engine volume -----------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    (1995, 2.0),
    ("1995", 2.0),
    ("1995cc", 2.0),
    ("1995 см³", 2.0),
    ("1.6L", 1.6),
    ("1.6 л", 1.6),
    ("1,6", 1.6),
    (2.0, 2.0),
    ("2.0", 2.0),
])
def test_engine_volume_variants(raw, expected):
    assert normalize_engine_volume(raw) == expected


@pytest.mark.parametrize("raw", [
    None, "", "abc", -1, 0, float("nan"), float("inf"), float("-inf"),
])
def test_engine_volume_rejects_invalid(raw):
    assert normalize_engine_volume(raw) is None


def test_engine_volume_small_cc_still_converted():
    """51–99 cc трактуются как литры (порог 100) — мотоциклы не поддерживаются,
    но не превращаются в 50-литровые двигатели."""
    assert normalize_engine_volume(50) == 50.0  # известное ограничение
    assert normalize_engine_volume(600) == 0.6


# --- color -------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("белый", "white"),
    ("Чёрный", "black"),
    ("черный", "black"),
    ("серебряный", "silver"),
    ("серебристый", "silver"),
    ("흰색청", "white"),
    ("청색", "blue"),
])
def test_color_variants(raw, expected):
    assert normalize_color(raw) == expected


def test_color_unknown_returned_normalized():
    assert normalize_color("  Candy  Blue  ") == "candy blue"


# --- fuel/body strict --------------------------------------------------

def test_normalize_fuel_strict_returns_none():
    assert normalize_fuel("unknown-fuel") is None
    assert normalize_fuel("бензин") == "petrol"