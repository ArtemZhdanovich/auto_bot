"""Карта классов адаптеров источников (ТЗ п. 6.4; GEO-V1 §1).

Код в Git, конфиг в БД: `sources.adapter_class` должен существовать
в этой карте, иначе запись источника отклоняется.

GEO-V1: dubizzle (ОАЭ) и cars_com (США) — за антиботом (Imperva/Akamai):
включать только с capabilities.use_proxy + резидентными прокси
(селекторы сверены на фикстурах, см. докстринги адаптеров).
"""

from app.infrastructure.sources.base import BaseApiAdapter
from app.infrastructure.sources.cars_com import CarsComHtmlAdapter
from app.infrastructure.sources.che168 import Che168HtmlAdapter
from app.infrastructure.sources.dubizzle import DubizzleHtmlAdapter
from app.infrastructure.sources.encar import EncarApiAdapter
from app.infrastructure.sources.kcar import KCarApiAdapter
from app.infrastructure.sources.kolesa import KolesaHtmlAdapter

ADAPTER_CLASSES: dict[str, type[BaseApiAdapter]] = {
    "EncarApiAdapter": EncarApiAdapter,
    "KCarApiAdapter": KCarApiAdapter,
    "Che168HtmlAdapter": Che168HtmlAdapter,
    "DubizzleHtmlAdapter": DubizzleHtmlAdapter,
    "CarsComHtmlAdapter": CarsComHtmlAdapter,
    "KolesaHtmlAdapter": KolesaHtmlAdapter,
}

__all__ = [
    "ADAPTER_CLASSES",
    "BaseApiAdapter",
    "CarsComHtmlAdapter",
    "Che168HtmlAdapter",
    "DubizzleHtmlAdapter",
    "EncarApiAdapter",
    "KCarApiAdapter",
    "KolesaHtmlAdapter",
]
