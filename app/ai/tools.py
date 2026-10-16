"""Tools AI-агента (ТЗ п. 7.3) и их выполнение.

LLM только предлагает патчи — финальную структуру собирает система:
валидация каждого patch через Pydantic (ТЗ п. 7.1).
"""

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.schemas.criteria import SearchCriteria
from app.schemas.dialog import DialogState

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

_CRITERIA_FIELDS = set(SearchCriteria.model_fields)

TOOLS: list[dict[str, Any]] = [
    {
        "name": "update_criteria",
        "description": (
            "Обновить параметры поиска на основе сказанного пользователем. "
            "Передавай только те поля, которые пользователь явно назвал. "
            "Не выдумывай значения."
        ),
        "parameters": SearchCriteria.model_json_schema(),
    },
    {
        "name": "ask_clarification",
        "description": (
            "Задать один уточняющий вопрос. Использовать, если критичного "
            "параметра не хватает. Не задавать больше одного вопроса за раз. "
            "Если можно — предложить варианты кнопками."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "field": {"type": "string"},
                "question": {"type": "string"},
                "options": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["field", "question"],
        },
    },
    {
        "name": "answer_question",
        "description": (
            "Ответить на вопрос пользователя про растаможку, доставку, пошлины, "
            "риски, FAQ по странам. Вопрос уходит в базу знаний (RAG), которая "
            "вернёт ответ из актуальных документов. Передавай question — "
            "переформулированный вопрос пользователя. Если знаешь ответ сам, "
            "можешь передать answer — он будет использован, только если база "
            "знаний недоступна или не нашла ответа. Не выдумывай цифры: если "
            "не уверен, ответь без answer — пользователь получит честную пометку."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "topic": {
                    "type": "string",
                    "enum": ["customs", "delivery", "risks", "fees", "faq"],
                },
                "question": {"type": "string"},
                "answer": {"type": "string"},
            },
            "required": ["topic", "question"],
        },
    },
    {
        "name": "lookup_reference",
        "description": (
            "Найти марку/модель/поколение в справочнике. "
            "Использовать, если пользователь назвал модель неточно."
        ),
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
    {
        "name": "preview_search",
        "description": (
            "Промежуточный поиск, чтобы показать 'вот что есть по текущим "
            "критериям'. Не финализирует поиск."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "criteria": SearchCriteria.model_json_schema(),
                "limit": {"type": "integer", "default": 3},
            },
            "required": ["criteria"],
        },
    },
    {
        "name": "calc_delivery",
        "description": (
            "Рассчитать стоимость авто из Кореи «под ключ» во Владивостоке: "
            "авто + море + брокер + СБКТС/ЭПТС + пошлина + сбор + утильсбор. "
            "Использовать, когда пользователь спрашивает про итоговую цену, "
            "растаможку, доставку или сравнивает варианты."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "price_usd": {"type": "number", "description": "Цена авто в USD"},
                "engine_cc": {"type": "integer", "description": "Объём двигателя в см³"},
                "year": {"type": "integer", "description": "Год выпуска"},
                "power_hp": {"type": "integer", "description": "Мощность в л.с. (влияет на утильсбор: льгота только до 160 л.с.)"},
            },
            "required": ["price_usd", "year"],
        },
    },
    {
        "name": "finalize_search",
        "description": (
            "Пользователь готов искать. Зафиксировать критерии и запустить "
            "полный парсинг по источникам."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "criteria": SearchCriteria.model_json_schema(),
            },
            "required": ["criteria"],
        },
    },
]


# --- выполнение tools ---------------------------------------------------


@dataclass
class ToolOutcome:
    """Результат выполнения tool."""

    text: str | None = None  # текст для пользователя (если tool его производит)
    options: list[str] = field(default_factory=list)  # кнопки
    asked_field: str | None = None
    finalized: bool = False


def apply_patch(state: DialogState, patch: dict[str, Any]) -> list[str]:
    """Валидация и применение патча к критериям. Возвращает применённые поля.

    Неизвестные поля игнорируются, None не затирает значения (ТЗ п. 7.1).
    """
    clean = {k: v for k, v in patch.items() if k in _CRITERIA_FIELDS and v is not None}
    validated = SearchCriteria(**clean)  # Pydantic-валидация значений
    dumped = validated.model_dump()
    valid = {k: dumped[k] for k in clean}  # только поля из патча, с коэрцией типов
    if not valid:
        return []
    state.criteria = state.criteria.model_copy(update=valid)
    return list(valid)


def has_critical_fields(criteria: SearchCriteria) -> bool:
    """Критичные поля финализации (ТЗ п. 7.4):
    make ИЛИ model, price_max, country ИЛИ sources."""
    return (
        bool(criteria.make or criteria.model)
        and criteria.price_max is not None
        and bool(criteria.country or criteria.sources)
    )


REFERENCE: dict[str, list[str]] = {
    "hyundai": ["Palisade", "Tucson", "Santa Fe", "Sonata", "Creta", "Staria"],
    "kia": ["Sorento", "Sportage", "Carnival", "K5", "Seltos", "Mohave"],
    "genesis": ["GV80", "GV70", "G80"],
    "bmw": ["X5", "X7", "3 Series", "5 Series"],
    "mercedes-benz": ["GLE", "GLS", "E-Class", "S-Class"],
    "toyota": ["Camry", "RAV4", "Highlander", "Land Cruiser"],
    "lexus": ["RX", "NX", "LX"],
}


def lookup_reference(query: str) -> str | None:
    q = query.strip().lower()
    if not q:
        return None
    for make, models in REFERENCE.items():
        if q in make:
            return f"{make.title()}: {', '.join(models)}"
        for model in models:
            if q in model.lower():
                return f"{make.title()} {model}"
    return None
