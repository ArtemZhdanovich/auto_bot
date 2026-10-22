"""Карта классов адаптеров источников (ТЗ п. 6.4).

Код в Git, конфиг в БД: `sources.adapter_class` должен существовать
в этой карте, иначе запись источника отклоняется.
"""

from app.infrastructure.sources.base import BaseApiAdapter
from app.infrastructure.sources.che168 import Che168HtmlAdapter
from app.infrastructure.sources.encar import EncarApiAdapter
from app.infrastructure.sources.kcar import KCarApiAdapter

# Новые источники (mobile_de, copart, iaai) — этапы 7+ (ТЗ п. 14.3)
ADAPTER_CLASSES: dict[str, type[BaseApiAdapter]] = {
    "EncarApiAdapter": EncarApiAdapter,
    "KCarApiAdapter": KCarApiAdapter,
    "Che168HtmlAdapter": Che168HtmlAdapter,
}

__all__ = [
    "ADAPTER_CLASSES",
    "BaseApiAdapter",
    "Che168HtmlAdapter",
    "EncarApiAdapter",
    "KCarApiAdapter",
]
