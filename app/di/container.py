"""Сборка Dishka-контейнера приложения.

Провайдеры регистрируются в порядке слоёв: настройки → инфраструктура →
репозитории → сценарии. `extra` идёт последним, поэтому его фабрики
перекрывают стандартные (подмена БД/Redis фейками в тестах).
"""

from __future__ import annotations

from dishka import AsyncContainer, Provider, Scope, make_async_container

from app.di.providers.adapters import AdapterProvider
from app.di.providers.broker import BrokerProvider
from app.di.providers.database import DatabaseProvider
from app.di.providers.redis import RedisProvider
from app.di.providers.repositories import RepositoryProvider
from app.di.providers.security import SecurityProvider
from app.di.providers.settings import SettingsProvider
from app.di.providers.use_cases import UseCaseProvider


def make_container(*extra: Provider) -> AsyncContainer:
    """Собрать контейнер с открытым APP-скоупом.

    Args:
        *extra: Дополнительные провайдеры; регистрируются последними и
            перекрывают стандартные зависимости с тем же ключом.

    Returns:
        `AsyncContainer`; закрывается через `await container.close()`.
    """
    return make_async_container(
        SettingsProvider(),
        DatabaseProvider(),
        BrokerProvider(),
        RedisProvider(),
        RepositoryProvider(),
        AdapterProvider(),
        SecurityProvider(),
        UseCaseProvider(),
        *extra,
        start_scope=Scope.APP,
    )
