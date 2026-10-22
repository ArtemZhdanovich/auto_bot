"""Совместимый entrypoint: `uvicorn app.main:app`.

Реальное приложение собирается в `app.main_api`.
"""

from app.main_api import app

__all__ = ["app"]
