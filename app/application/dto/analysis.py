"""DTO AI-анализа цены: вход сценария и тексты отказов."""

from __future__ import annotations

from dataclasses import dataclass

GUEST_FORBIDDEN_MESSAGE = (
    "guest_forbidden: AI-анализ цены доступен только после входа или регистрации"
)
QUOTA_EXCEEDED_MESSAGE = (
    "quota_exceeded: 1 разбор этой машины в неделю на Free. Безлимит — в Pro"
)


@dataclass(frozen=True, slots=True)
class AnalysisCommand:
    """Запрос разбора: кто, по какой машине и через какой канал."""

    user_id: int
    listing_id: int
    channel: str = "web"
