"""Провайдер RabbitMQ: брокер и публикаторы.

`RabbitBroker` живёт весь процесс (APP), но соединение открывается только
при `broker.start()` в entrypoint — импорт модуля не должен ходить в сеть.
"""

from __future__ import annotations

from dishka import Provider, Scope, provide
from faststream.rabbit import RabbitBroker

from app.config import Settings
from app.infrastructure.messaging.publisher import (
    BufferedEventPublisher,
    FastStreamRawPublisher,
    RawEventPublisher,
)


class BrokerProvider(Provider):
    """APP: брокер и «сырой» публикатор; REQUEST: буфер событий."""

    @provide(scope=Scope.APP)
    def broker(self, settings: Settings) -> RabbitBroker:
        """Создать брокер без подключения (start — в lifespan/воркере)."""
        return RabbitBroker(
            settings.rabbitmq_url,
            graceful_timeout=30.0,
        )

    @provide(scope=Scope.APP)
    def raw_publisher(self, broker: RabbitBroker) -> RawEventPublisher:
        """Публикатор для outbox-релея."""
        return FastStreamRawPublisher(broker)

    @provide(scope=Scope.REQUEST)
    def publisher(self, raw: RawEventPublisher) -> BufferedEventPublisher:
        """Буфер событий запроса: flush вызывает UoW после commit."""
        return BufferedEventPublisher(raw)
