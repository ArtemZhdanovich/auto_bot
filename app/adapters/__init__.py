"""Карта классов адаптеров (ТЗ п. 6.4).

Код в Git, конфиг в БД: `sources.adapter_class` должен существовать
в этой карте, иначе запись источника отклоняется.
"""

from app.adapters.base import BaseApiAdapter
from app.adapters.che168 import Che168HtmlAdapter
from app.adapters.encar import EncarApiAdapter
from app.adapters.kcar import KCarApiAdapter

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

