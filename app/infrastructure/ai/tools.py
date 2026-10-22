"""Tools AI-агента (ТЗ п. 7.3) и их выполнение.

LLM только предлагает патчи — финальную структуру собирает система:
валидация каждого patch через Pydantic (ТЗ п. 7.1).

Ключевые инварианты:
- неизвестные значения справочников **отбрасываются** (не уходят в state);
- cross-field валидаторы `SearchCriteria` перезапускаются после `model_copy`;
- ошибка валидации одного поля не отбрасывает остальные.
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from pydantic import ValidationError

from app.infrastructure.sources.common.normalize import (
    normalize_body,
    normalize_color,
    normalize_drive,
    normalize_fuel,
    normalize_make,
    normalize_seller_type,
    normalize_transmission,
)
from app.domain.search.criteria import SearchCriteria
from app.domain.dialog.state import DialogState

logger = logging.getLogger(__name__)

_CRITERIA_FIELDS = set(SearchCriteria.model_fields)

# Справочники значений для кнопок-опций (пп. «ИИ работает по инструментарию,
# а не выдумывает варианты»). Кнопки агента для этих полей всегда из этого
# списка — LLM не предлагает свои. Значения совпадают с ключами normalize_*,
# поэтому клик по кнопке нормализуется в apply_patch без доп. маппинга.
FIELD_OPTIONS: dict[str, list[str]] = {
    "body_type": [
        "седан", "внедорожник", "хэтчбек", "универсал",
        "купе", "минивэн", "пикап", "кабриолет",
    ],
    "fuel": ["бензин", "дизель", "гибрид", "электро", "газ"],
    "transmission": ["автомат", "механика", "вариатор", "робот"],
    "drive": ["передний", "задний", "полный"],
    "color": [
        "чёрный", "белый", "серебристый", "серый", "синий", "красный",
        "коричневый", "зелёный", "бежевый", "жёлтый", "оранжевый", "золотой",
    ],
    "seller_type": ["частник", "дилер", "аукцион"],
}

# Нормализация значений патча: LLM пишет «бензин»/«седан»/«чёрный» — в критерии
# кладём канон (petrol/sedan/black), как в listings.
# ВАЖНО: для strict-нормализаторов (fuel/body/transmission/drive/seller_type)
# `None` от нормализатора = «неизвестное значение» → поле отбрасывается.
# Исключение — `color` (lenient: возвращает lowercased строку для неизвестного).
_PATCH_NORMALIZERS: dict[str, Callable[[str | None], str | None]] = {
    "body_type": normalize_body,
    "fuel": normalize_fuel,
    "transmission": normalize_transmission,
    "drive": normalize_drive,
    "color": normalize_color,
    "seller_type": normalize_seller_type,
}

# Нормализаторы, для которых `None` = drop field (strict). `color` — не strict.
_STRICT_NORMALIZERS = frozenset({"body_type", "fuel", "transmission", "drive", "seller_type"})

# «Пустые» значения, которые не должны затирать критерии (только через
# явный сброс, если понадобится — отдельным патчем с `[]` и флагом).
_EMPTY_VALUES: tuple[Any, ...] = (None, "", [], {})


def _criteria_schema_with_enums() -> dict[str, Any]:
    """Схема SearchCriteria с enum из справочников — LLM видит допустимые
    значения прямо в tool-схеме и не выдумывает свои (MCP-подход).

    Returns:
        JSON-схема для tool-параметров.
    """
    schema = SearchCriteria.model_json_schema()
    props = schema.get("properties", {})
    for field_name, options in FIELD_OPTIONS.items():
        if field_name in props:
            props[field_name]["enum"] = options
    return schema


TOOLS: tuple[dict[str, Any], ...] = (
    {
        "name": "update_criteria",
        "description": (
            "Обновить параметры поиска на основе сказанного пользователем. "
            "Передавай только те поля, которые пользователь явно назвал. "
            "Не выдумывай значения."
        ),
        "parameters": _criteria_schema_with_enums(),
    },
    {
        "name": "ask_clarification",
        "description": (
            "Задать один уточняющий вопрос. Использовать, если критичного "
            "параметра не хватает. Не задавать больше одного вопроса за раз. "
            "Если можно — предложить варианты кнопками. Для полей с фиксированным "
            "набором значений (body_type, fuel, transmission, drive, seller_type) "
            "система сама подставит справочные кнопки — свои options не выдумывай."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "field": {"type": "string"},
                "question": {"type": "string", "maxLength": 500},
                "options": {
                    "type": "array",
                    "items": {"type": "string", "maxLength": 64},
                    "maxItems": 5,
                },
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
                "question": {"type": "string", "maxLength": 1000},
                "answer": {"type": "string", "maxLength": 1000},
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
            "properties": {"query": {"type": "string", "maxLength": 100}},
            "required": ["query"],
        },
    },
    {
        "name": "preview_search",
        "description": (
            "Промежуточный поиск по реальной базе объявлений, чтобы показать "
            "'вот что есть по текущим критериям'. Не финализирует поиск. "
            "Результаты — только реальные объявления, ничего не выдумывай."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "criteria": _criteria_schema_with_enums(),
                "limit": {
                    "type": "integer",
                    "default": 3,
                    "minimum": 1,
                    "maximum": 10,
                },
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
                "price_usd": {
                    "type": "number", "minimum": 100, "description": "Цена авто в USD",
                },
                "engine_cc": {
                    "type": "integer", "minimum": 50, "maximum": 10000,
                    "description": "Объём двигателя в см³",
                },
                "year": {
                    "type": "integer", "minimum": 1980, "maximum": 2100,
                    "description": "Год выпуска",
                },
                "power_hp": {
                    "type": "integer", "minimum": 10, "maximum": 2000,
                    "description": "Мощность в л.с. (влияет на утильсбор: льгота только до 160 л.с.)",
                },
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
                "criteria": _criteria_schema_with_enums(),
            },
            "required": ["criteria"],
        },
    },
)


# --- выполнение tools ---------------------------------------------------


@dataclass
class ToolOutcome:
    """Результат выполнения tool."""

    text: str | None = None  # текст для пользователя (если tool его производит)
    options: list[str] = field(default_factory=list)  # кнопки
    asked_field: str | None = None
    finalized: bool = False


def _normalize_patch_field(field_name: str, raw_value: Any) -> tuple[bool, Any]:
    """Нормализует одно поле патча.

    Для strict-нормализаторов (`fuel`/`body_type`/…) неизвестное значение
    **отбрасывается** — иначе мусор уходит в `state.criteria` и фильтры
    перестают работать. `color` — lenient (возвращает lowercase строку).

    Args:
        field_name: Имя поля.
        raw_value: Сырое значение от LLM.

    Returns:
        `(keep, value)` — `keep=False` означает «поле отбросить».
    """
    normalizer = _PATCH_NORMALIZERS.get(field_name)
    if normalizer is None:
        return True, raw_value  # не справочное поле — оставляем как есть
    if not isinstance(raw_value, str):
        return True, raw_value  # не строка — пусть Pydantic решает (coerce/fail)
    normalized = normalizer(raw_value)
    if normalized is not None:
        return True, normalized
    if field_name in _STRICT_NORMALIZERS:
        logger.info(
            "patch_field_dropped: field=%s value=%r (unknown enum)",
            field_name, raw_value,
        )
        return False, None
    # lenient (color): fallback на raw
    return True, raw_value


def apply_patch(state: DialogState, patch: dict[str, Any]) -> list[str]:
    """Валидация и применение патча к критериям. Возвращает применённые поля.

    Гарантии:
    - неизвестные поля игнорируются;
    - `None`/`""`/`[]`/`{}` не затирают существующие значения;
    - значения из справочников нормализуются; неизвестные **отбрасываются**
      (кроме `color`, который lenient);
    - ошибка валидации одного поля не отбрасывает остальные;
    - cross-field валидаторы `SearchCriteria` перезапускаются после merge.

    Args:
        state: `DialogState` (мутируется `state.criteria`).
        patch: Патч от LLM.

    Returns:
        Список применённых полей.
    """
    # 1. Базовая фильтрация: известные поля, непустые значения.
    clean: dict[str, Any] = {}
    for key, value in patch.items():
        if key not in _CRITERIA_FIELDS:
            continue
        if value in _EMPTY_VALUES:
            continue
        keep, normalized = _normalize_patch_field(key, value)
        if not keep:
            continue
        clean[key] = normalized

    if not clean:
        return []

    # 2. Пофайловая валидация через Pydantic: одно битое поле не роняет патч.
    validated_fields: dict[str, Any] = {}
    for field_name, value in clean.items():
        try:
            one = SearchCriteria(**{field_name: value})
        except ValidationError as exc:
            logger.info(
                "patch_field_invalid: field=%s value=%r errors=%s",
                field_name, value, exc.errors(),
            )
            continue
        dumped = one.model_dump()
        if field_name in dumped:
            validated_fields[field_name] = dumped[field_name]

    if not validated_fields:
        return []

    # 3. Cross-field валидация: применяем к текущему state и пере-валидируем.
    #    `model_copy` не запускает model_validator — делаем это явно.
    merged = {**state.criteria.model_dump(), **validated_fields}
    try:
        new_criteria = SearchCriteria(**merged)
    except ValidationError as exc:
        logger.info(
            "patch_cross_field_invalid: applied=%s errors=%s",
            list(validated_fields), exc.errors(),
        )
        return []

    state.criteria = new_criteria
    return list(validated_fields)


def has_critical_fields(criteria: SearchCriteria) -> bool:
    """Критичные поля финализации (ТЗ п. 7.4):
    make ИЛИ model, price_max, country ИЛИ sources.

    Args:
        criteria: Критерии поиска.

    Returns:
        `True`, если все критические поля заданы.
    """
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
    """Ищет марку/модель в справочнике по подстроке.

    Матч требует минимум 2 символа (защита от `"a"` → все марки).
    Сначала проверяются марки (canonical name), затем модели. Возвращает
    каноническое имя бренда (`BMW`, а не `Bmw`).

    Args:
        query: Запрос пользователя.

    Returns:
        Строка вида `"BMW: X5, X7, ..."` или `"Toyota Camry"` или `None`.
    """
    q = query.strip().lower()
    if len(q) < 2:
        return None
    for make, models in REFERENCE.items():
        canonical_make = normalize_make(make) or make
        if q in make or q in canonical_make.lower():
            return f"{canonical_make}: {', '.join(models)}"
        for model in models:
            if q in model.lower():
                return f"{canonical_make} {model}"
    return None