"""Тесты tools: валидация патчей, критичные поля, справочник, FAQ."""

from decimal import Decimal

from app.ai.tools import apply_patch, has_critical_fields, lookup_reference
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


def test_faq_matching_requires_question():
    assert match_faq_topic("Сколько стоит растаможка?") == "customs_kr_ru"
    assert match_faq_topic("Какие риски на аукционе?") == "risks_auction"
    assert match_faq_topic("про доставку расскажи") == "delivery_kr_vladivostok"
    # не вопрос → FAQ не мешает сбору критериев
    assert match_faq_topic("Хочу палисайд до 45000") is None
