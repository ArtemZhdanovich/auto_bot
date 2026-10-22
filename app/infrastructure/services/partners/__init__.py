"""Партнёрский контур: SQL-репозиторий + rate limit.

`repository.py` — `SqlAlchemyPartnerRepository` (порты из
`app.application.partners`), `rate_limit.py` — фиксированное окно на Redis.
Легаси-фасады удалены: сборку графа делают DI-контейнер и тесты
(`tests/partners_helpers.py`).
"""

from __future__ import annotations

from app.infrastructure.services.partners.rate_limit import (  # noqa: F401
    check_rate_limit,
)
from app.infrastructure.services.partners.repository import (  # noqa: F401
    SqlAlchemyPartnerRepository,
)

__all__ = [
    "SqlAlchemyPartnerRepository",
    "check_rate_limit",
]
