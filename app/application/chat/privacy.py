"""Маскировка контактов в сообщениях чатов (08 §3).

Паттерны телефонов/мессенджеров вынесены в общий доменный модуль
`app.domain.shared.contacts` — их переиспользует авто-модерация лотов
рынка (10 §2: правило `contacts_in_text` в описании лота). Здесь
остаётся публичный контракт чата: `mask_contacts` и плейсхолдеры.

Чистая функция без обращений к БД: телефоны и ссылки внешних мессенджеров
заменяются плейсхолдерами ДО валидации длины — обмен контактами только
через сделку. Support-треды не маскируются (оператор = площадка) —
применение ограничивает `SendMessage` по типу треда.
"""

from __future__ import annotations

from app.domain.shared.contacts import (
    MESSENGER_MASK,
    PHONE_MASK,
    PHONE_RE,
    TME_RE,
    WHATSAPP_RE,
    mask_contacts,
)

__all__ = [
    "MESSENGER_MASK",
    "PHONE_MASK",
    "PHONE_RE",
    "TME_RE",
    "WHATSAPP_RE",
    "mask_contacts",
]
