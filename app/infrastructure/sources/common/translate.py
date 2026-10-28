"""Общий перевод хангыля для адаптеров источников (encar/kcar).

Глоссарий канонизирует известные токены (марки/модели/цвета), остальное
уходит батчем в NLLB-микросервис. Ошибки/выключенный сервис оставляют
оригинал: перевод некритичен и не должен ронять fetch.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from app.domain.translation.glossary import apply_glossary

logger = logging.getLogger(__name__)

# Корейский текст (хангыль) — для выборки строк под перевод.
HANGUL_RE = re.compile(r"[\uAC00-\uD7A3]")

# Потолок длины строки в батче (микросервис ограничивает input).
_MAX_TEXT_LEN = 500


def collect_hangul(obj: Any, out: dict[str, str]) -> None:
    """Собрать все строки с хангылем из dict/list/str (рекурсивно)."""
    if isinstance(obj, dict):
        for v in obj.values():
            collect_hangul(v, out)
    elif isinstance(obj, list):
        for v in obj:
            collect_hangul(v, out)
    elif isinstance(obj, str) and HANGUL_RE.search(obj):
        out.setdefault(obj, obj)


def replace_hangul(obj: Any, fixed: dict[str, str]) -> None:
    """Заменить строки с хангылем на перевод (in-place, рекурсивно)."""
    if isinstance(obj, dict):
        for key, v in obj.items():
            if isinstance(v, str) and v in fixed:
                obj[key] = fixed[v]
            else:
                replace_hangul(v, fixed)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            if isinstance(v, str) and v in fixed:
                obj[i] = fixed[v]
            else:
                replace_hangul(v, fixed)


async def translate_hangul(translator: Any, *objs: Any) -> None:
    """Перевести корейские строки в структурах (in-place).

    Известные токены канонизирует глоссарий, остальное уходит в
    микросервис. `translator is None` (перевод выключен) или любая ошибка
    → строки остаются как есть.
    """
    if translator is None:
        return
    strings: dict[str, str] = {}
    for obj in objs:
        collect_hangul(obj, strings)
    if not strings:
        return
    originals = list(strings)
    prepared = [apply_glossary(s)[:_MAX_TEXT_LEN] for s in originals]
    try:
        translated = await translator.translate(
            prepared, source_lang="ko", target_lang="ru"
        )
    except Exception as exc:  # noqa: BLE001 — перевод не должен ронять fetch
        logger.warning("translate_hangul_failed n=%d: %s", len(prepared), exc)
        return
    fixed = {
        orig: (translated[i] or orig) if i < len(translated or []) else orig
        for i, orig in enumerate(originals)
    }
    for obj in objs:
        replace_hangul(obj, fixed)
