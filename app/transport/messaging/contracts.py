"""Команды, которые приходят в очереди воркеров.

Модели строгие (`extra="forbid"`): лишнее поле в сообщении — ошибка
контракта, а не молча проигнорированные данные. FastStream валидирует тело
сообщения этой моделью до вызова обработчика.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class EnrichListingCommand(BaseModel):
    """Догрузить спеки лота из detail-источника.

    Attributes:
        listing_id: PK лота в `listings`.
        idempotency_key: Ключ идемпотентности доставки; минимум 8 символов,
            чтобы случайный/пустой ключ не склеил разные сообщения.
    """

    model_config = ConfigDict(extra="forbid")

    listing_id: int = Field(gt=0)
    idempotency_key: str = Field(min_length=8)
