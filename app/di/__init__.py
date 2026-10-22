"""Слой композиции зависимостей (Dishka).

Точка входа — `make_container`: собирает провайдеры настроек, БД, Redis,
репозиториев и сценариев. Существующий код (`app/main.py`, роутеры,
`app/core/deps*.py`) не меняется: слой подключается отдельно.
"""

from app.di.container import make_container

__all__ = ["make_container"]
