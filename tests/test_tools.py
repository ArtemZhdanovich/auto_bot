"""Тесты tools: валидация патчей, критичные поля, справочник, FAQ."""

from decimal import Decimal

from app.ai.tools import (
    FIELD_OPTIONS,
    TOOLS,
    apply_patch,
    has_critical_fields,
    lookup_reference,
)
from app.schemas.criteria import SearchCriteria
from app.services.dialog import match_faq_topic, new_state


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


def test_apply_patch_keeps_unknown_spec_value():
    """Нераспознанное значение не роняет патч — кладётся как есть."""
    state = new_state(1)
    apply_patch(state, {"body_type": "лифтбек"})
    assert state.criteria.body_type == "лифтбек"


def test_field_options_are_reference_catalogs():
    """Каждая кнопка-опция нормализуется в канон справочника."""
    from app.adapters.common.normalize import (
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
