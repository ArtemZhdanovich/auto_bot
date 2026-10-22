"""Каталог объявлений (listings + favorites + filters + notifications).

Контракты сервиса собраны здесь явно: `ports.py` — интерфейсы хранилищ,
очереди, object storage и перевода; `dto.py` — лента, фильтры, ингест,
AI-анализ цены. Сами сценарии — в пакетах `listings`, `favorites`,
`filters`, `notifications`, `search`, `analysis`, `pricing`, `translation`.
"""
