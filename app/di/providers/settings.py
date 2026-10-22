"""Провайдер настроек приложения.

`get_settings` кэширован (`lru_cache`), поэтому фабрика живёт в APP-скоупе:
один объект `Settings` на контейнер, без повторного чтения `.env`.
"""

from __future__ import annotations

from dishka import Provider, Scope, provide

from app.config import Settings, get_settings


class SettingsProvider(Provider):
    """Отдаёт `Settings` в APP-скоупе."""

    scope = Scope.APP

    @provide
    def settings(self) -> Settings:
        """Вернуть настройки приложения из `get_settings` (кэш процесса)."""
        return get_settings()
