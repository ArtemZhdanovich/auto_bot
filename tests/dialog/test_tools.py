"""Тесты tools: валидация патчей, критичные поля, справочник, FAQ."""

from decimal import Decimal

from app.infrastructure.ai.tools import (
    FIELD_OPTIONS,
    TOOLS,
    apply_patch,
    has_critical_fields,
    lookup_reference,
)
from app.domain.search.criteria import SearchCriteria
from app.domain.dialog.faq import match_faq_topic
from app.infrastructure.services.dialog import new_state


def test_apply_patch_merges_and_validates():
    state = new_state(1)
    applied = apply_patch(
        state, {"make": "Kia", "model": "Sorento", "year_from": 2022}
    )
    assert set(applied) == {"make", "model", "year_from"}
    assert state.criteria.model == "Sorento"
    assert state.criteria.year_from == 2022


def test_apply_patch_ignores_unknown_and_none():
    state = new_state(1)
    applied = apply_patch(
        state, {"unknown_field": "x", "make": None, "year_from": 2020}
    )
    assert applied == ["year_from"]
    assert state.criteria.make is None  # None не затирает


def test_apply_patch_merges_into_existing():
    state = new_state(1)
    state.criteria = SearchCriteria(make="Kia")
    apply_patch(state, {"price_max": 45000})
    assert state.criteria.make == "Kia"  # существующее не потеряно
    assert state.criteria.price_max == 45000


def test_has_critical_fields():
    base = {"make": "Kia", "price_max": Decimal("45000")}
    assert not has_critical_fields(SearchCriteria(**base))  # нет country/sources
    assert has_critical_fields(SearchCriteria(**base, country="KR"))
    assert has_critical_fields(SearchCriteria(**base, sources=["encar"]))
    assert not has_critical_fields(SearchCriteria(model="X", price_max=Decimal("1")))
    assert not has_critical_fields(SearchCriteria(make="Kia", country="KR"))


def test_lookup_reference():
    assert "Sorento" in (lookup_reference("sorento") or "")
    assert "Hyundai" in (lookup_reference("hyundai") or "")
    assert "Genesis GV80" in (lookup_reference("gv80") or "")


def test_lookup_reference_no_false_positive():
    """TECHDEBT 4.5: подстрока больше не матчит — `"ai"` не Hyundai."""
    assert lookup_reference("ai") is None
    assert lookup_reference("unda") is None
    # префикс по-прежнему работает
    assert lookup_reference("hyu") is not None
    assert lookup_reference("абракадабра") is None
    assert lookup_reference("") is None


# --- справочники значений (MCP-подход: опции из инструментария) ----------


def test_apply_patch_normalizes_catalog_values():
    state = new_state(1)
    applied = apply_patch(
        state,
        {
            "body_type": "внедорожник",
            "fuel": "бензин",
            "transmission": "автомат",
            "drive": "полный",
            "seller_type": "частник",
        },
    )
    assert set(applied) == {"body_type", "fuel", "transmission", "drive", "seller_type"}
    assert state.criteria.body_type == "suv"
    assert state.criteria.fuel == "petrol"
    assert state.criteria.transmission == "automatic"
    assert state.criteria.drive == "awd"
    assert state.criteria.seller_type == "private"


def test_apply_patch_drops_unknown_spec_value():
    """Нераспознанное значение справочника отбрасывается (strict-нормализатор)."""
    state = new_state(1)
    applied = apply_patch(state, {"body_type": "лифтбек"})
    assert applied == []
    assert state.criteria.body_type is None


def test_field_options_are_reference_catalogs():
    """Каждая кнопка-опция нормализуется в канон справочника."""
    from app.infrastructure.sources.common.normalize import (
        normalize_body,
        normalize_drive,
        normalize_fuel,
        normalize_seller_type,
        normalize_transmission,
    )

    checks = {
        "body_type": normalize_body,
        "fuel": normalize_fuel,
        "transmission": normalize_transmission,
        "drive": normalize_drive,
        "seller_type": normalize_seller_type,
    }
    for field, normalizer in checks.items():
        for option in FIELD_OPTIONS[field]:
            assert normalizer(option), f"{field}: {option} не нормализуется"


def test_tools_schema_has_enums():
    """LLM видит допустимые значения прямо в tool-схеме."""
    update = next(t for t in TOOLS if t["name"] == "update_criteria")
    assert update["parameters"]["properties"]["body_type"]["enum"] == FIELD_OPTIONS["body_type"]
    preview = next(t for t in TOOLS if t["name"] == "preview_search")
    assert "enum" in preview["parameters"]["properties"]["criteria"]["properties"]["fuel"]


def test_faq_matching_requires_question():
    assert match_faq_topic("Сколько стоит растаможка?") == "customs_kr_ru"
    assert match_faq_topic("Какие риски на аукционе?") == "risks_auction"
    assert match_faq_topic("про доставку расскажи") == "delivery_kr_vladivostok"
    # не вопрос → FAQ не мешает сбору критериев
    assert match_faq_topic("Хочу палисайд до 45000") is None


# ---------- цвет: нормализация патча + enum в tool-схеме ----------


def test_apply_patch_color_normalized():
    """«чёрный»/«чёрная» → канон 'black', как в listings (фикс «цвета не находит»)."""
    state = new_state(1)
    applied = apply_patch(state, {"color": "чёрный"})
    assert applied == ["color"]
    assert state.criteria.color == "black"


def test_apply_patch_color_variants():
    state = new_state(1)
    apply_patch(state, {"color": "серебристый"})
    assert state.criteria.color == "silver"
    apply_patch(state, {"color": "синяя"})
    assert state.criteria.color == "синяя"  # «синяя» нет в словаре → как есть


def test_color_enum_in_tool_schema():
    """LLM видит справочник цветов прямо в схеме update_criteria."""
    tool = next(t for t in TOOLS if t["name"] == "update_criteria")
    assert tool["parameters"]["properties"]["color"]["enum"] == FIELD_OPTIONS["color"]


# tests/ai/test_tools.py
import pytest

from app.domain.dialog.state import DialogState


def _state(**criteria) -> DialogState:
    return DialogState(
        session_id="s", user_id=1, criteria=SearchCriteria(**criteria),
    )


# --- apply_patch: strict normalizer drops unknown --------------------

def test_apply_patch_unknown_body_type_dropped():
    """«купе-стрела» не должно попасть в state.criteria."""
    state = _state()
    applied = apply_patch(state, {"body_type": "купе-стрела", "make": "Toyota"})
    assert "body_type" not in applied
    assert state.criteria.body_type is None
    assert state.criteria.make == "Toyota"  # валидное поле применено


def test_apply_patch_known_body_type_applied():
    state = _state()
    applied = apply_patch(state, {"body_type": "седан"})
    assert "body_type" in applied
    assert state.criteria.body_type == "sedan"  # нормализовано


def test_apply_patch_unknown_fuel_dropped_not_raw():
    """Раньше: fuel='водород' уходил в state как есть."""
    state = _state()
    apply_patch(state, {"fuel": "водород"})
    assert state.criteria.fuel is None


# --- apply_patch: partial validation ---------------------------------

def test_apply_patch_invalid_field_does_not_drop_valid():
    """Год 999999 не должен убивать make."""
    state = _state()
    applied = apply_patch(state, {"make": "Toyota", "year_from": 999999})
    assert "make" in applied
    assert "year_from" not in applied
    assert state.criteria.make == "Toyota"


# --- apply_patch: cross-field validation -----------------------------

def test_apply_patch_cross_field_rejected():
    """price_min=1000 при price_max=500 — невалидная комбинация."""
    state = _state(price_max=500)
    applied = apply_patch(state, {"price_min": 1000})
    assert applied == []
    assert state.criteria.price_max == 500  # state не тронут
    assert state.criteria.price_min is None


def test_apply_patch_cross_field_ok():
    state = _state(price_max=5000)
    applied = apply_patch(state, {"price_min": 1000})
    assert "price_min" in applied
    assert state.criteria.price_min == 1000
    assert state.criteria.price_max == 5000


# --- apply_patch: empty values don't wipe ----------------------------

@pytest.mark.parametrize("empty", [None, "", [], {}])
def test_apply_patch_empty_does_not_wipe(empty):
    state = _state(make="Toyota", sources=["encar"])
    apply_patch(state, {"sources": empty})
    assert state.criteria.sources == ["encar"]


# --- apply_patch: unknown fields ignored -----------------------------

def test_apply_patch_unknown_field_ignored():
    state = _state()
    applied = apply_patch(state, {"unknown_field": "x", "make": "Kia"})
    assert applied == ["make"]


# --- apply_patch: empty patch ----------------------------------------

def test_apply_patch_empty_returns_empty():
    state = _state()
    assert apply_patch(state, {}) == []


# --- lookup_reference ------------------------------------------------

def test_lookup_reference_canonical_bmw():
    """«bmw».title() давал «Bmw» — теперь канон «BMW»."""
    result = lookup_reference("bmw")
    assert result is not None
    assert result.startswith("BMW:")
    assert "Bmw" not in result


def test_lookup_reference_single_char_rejected():
    assert lookup_reference("a") is None
    assert lookup_reference("n") is None


def test_lookup_reference_model_match():
    assert "Camry" in (lookup_reference("camry") or "")


def test_lookup_reference_empty_rejected():
    assert lookup_reference("") is None
    assert lookup_reference("  ") is None


# --- has_critical_fields ---------------------------------------------

def test_has_critical_fields_all_present():
    c = SearchCriteria(make="Toyota", price_max=50000, country="korea")
    assert has_critical_fields(c) is True


def test_has_critical_fields_missing_price():
    c = SearchCriteria(make="Toyota", country="korea")
    assert has_critical_fields(c) is False


def test_has_critical_fields_model_or_make():
    c = SearchCriteria(model="Camry", price_max=50000, sources=["encar"])
    assert has_critical_fields(c) is True


# --- TOOLS: schema constraints ---------------------------------------

def test_tools_is_immutable():
    # TOOLS — tuple: append даёт AttributeError (что строже ожидаемого TypeError).
    with pytest.raises((TypeError, AttributeError)):
        TOOLS.append({})  # tuple


def test_preview_search_limit_bounds():
    tool = next(t for t in TOOLS if t["name"] == "preview_search")
    limit = tool["parameters"]["properties"]["limit"]
    assert limit["minimum"] == 1
    assert limit["maximum"] == 10


def test_calc_delivery_year_bounds():
    tool = next(t for t in TOOLS if t["name"] == "calc_delivery")
    props = tool["parameters"]["properties"]
    assert props["year"]["minimum"] == 1980
    assert props["year"]["maximum"] == 2100