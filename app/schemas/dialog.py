"""DialogState — состояние AI-диалога (ТЗ п. 5.4)."""

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.criteria import SearchCriteria


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Message(BaseModel):
    role: Literal["user", "assistant", "system", "tool"]
    content: str
    timestamp: datetime = Field(default_factory=_utcnow)
    tool_name: str | None = None


class DialogState(BaseModel):
    session_id: str
    user_id: int

    # Накопленные параметры
    criteria: SearchCriteria = Field(default_factory=SearchCriteria)

    # Прогресс сбора
    asked_fields: list[str] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    freeform_notes: list[str] = Field(default_factory=list)
    answered_questions: list[str] = Field(default_factory=list)

    # История
    messages: list[Message] = Field(default_factory=list)

    # Статус
    status: Literal["collecting", "ready", "searching", "done"] = "collecting"

    # Метаданные
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)
    turn_count: int = 0
