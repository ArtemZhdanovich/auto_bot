"""Тесты нормализации (ТЗ п. 6.3)."""

from app.adapters.common.normalize import (
    normalize_body,
    normalize_drive,
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
