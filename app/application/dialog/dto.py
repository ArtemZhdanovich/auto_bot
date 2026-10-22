"""DTO диалогового контура: диалог, AI-поиск, контракт RAG-сервиса.

Ни ORM, ни HTTP: только данные. `UserProfile` — единственное представление
пользователя, которое видят сценарии (включая AI-поиск и AI-анализ), поэтому
`is_active`/`tier` уже разрешены адаптером, а не считаются в контроллере.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Диалог (dto/dialog)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class UserProfile:
    """Пользователь с точки зрения сценария."""

    user_id: int
    is_active: bool = True
    tier: str = "free"
    email: str | None = None

    @property
    def is_guest(self) -> bool:
        """Гостевой аккаунт сайта: синтетический id < 0 и без email."""
        return self.user_id < 0 and not self.email


@dataclass(frozen=True, slots=True)
class DialogMessageCommand:
    """Один ход диалога."""

    user_id: int
    text: str


@dataclass(frozen=True, slots=True)
class DialogReply:
    """Ответ агента на один ход (контракт `DialogReplyResponse`)."""

    session_id: str
    text: str
    options: tuple[str, ...] = ()
    status: str = "collecting"
    finalize_ready: bool = False
    turn_count: int = 0
    criteria: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class DialogRestoreResult:
    """Результат восстановления диалога из архива."""

    restored: bool
    reason: str | None = None
    session_id: str | None = None


@dataclass(frozen=True, slots=True)
class DialogFinalizeCommand:
    """Финализация: пользователь и (опционально) явные критерии."""

    user_id: int
    criteria: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class DialogFinalizeResult:
    """Зафиксированные критерии и источники, куда поставлена задача."""

    criteria: dict[str, Any]
    sources: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DialogSessionRecord:
    """Строка архива диалога (`dialog_sessions`) без ORM."""

    session_id: str
    user_id: int
    status: str
    criteria: dict[str, Any] = field(default_factory=dict)
    turn_count: int = 0
    messages: tuple[dict[str, Any], ...] = ()
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class DialogSessionView:
    """Архивный диалог, отданный владельцу."""

    session_id: str
    status: str
    criteria: dict[str, Any] = field(default_factory=dict)
    turn_count: int = 0
    messages: tuple[dict[str, Any], ...] = ()
    created_at: datetime | None = None
    updated_at: datetime | None = None


# ---------------------------------------------------------------------------
# AI-поиск Mini App (dto/ai_search)
# ---------------------------------------------------------------------------

# Rate-limit на сообщения: защита бюджета от «бесплатных» LLM-вызовов.
AI_MESSAGE_RATE_LIMIT = 20
AI_MESSAGE_RATE_WINDOW_SEC = 60
AI_MESSAGE_RATE_PREFIX = "ai_msg_rl"


@dataclass(frozen=True, slots=True)
class AiMessageCommand:
    """Сообщение пользователю Mini App (пользователь — из JWT)."""

    user_id: int
    text: str


# ---------------------------------------------------------------------------
# Контракт RAG-сервиса (dto/rag, план внедрения RAG п. 6.3)
# ---------------------------------------------------------------------------


class RAGSource(BaseModel):
    """Источник, из которого собран ответ (документ + чанк)."""

    doc_id: str
    chunk_id: str
    score: float
    title: str | None = None
    snippet: str | None = None


class RAGAnswer(BaseModel):
    """Ответ RAG-сервиса на POST /api/v1/ask."""

    answer: str
    sources: list[RAGSource] = Field(default_factory=list)
    # null от сервиса допустим: «уверенность не посчитана».
    confidence: float | None = 0.0
    latency_ms: int = 0
    cached: bool = False


class RAGIngestResult(BaseModel):
    """Ответ на POST /api/v1/ingest (асинхронная индексация)."""

    doc_id: str
    status: str = "queued"
    estimated_time_sec: int | None = None


class RAGAskRequest(BaseModel):
    """Тело запроса к /api/v1/ask (план п. 6.1)."""

    question: str
    context: dict[str, Any] = Field(default_factory=dict)


__all__ = [
    "AI_MESSAGE_RATE_LIMIT",
    "AI_MESSAGE_RATE_PREFIX",
    "AI_MESSAGE_RATE_WINDOW_SEC",
    "AiMessageCommand",
    "DialogFinalizeCommand",
    "DialogFinalizeResult",
    "DialogMessageCommand",
    "DialogReply",
    "DialogRestoreResult",
    "DialogSessionRecord",
    "DialogSessionView",
    "RAGAnswer",
    "RAGAskRequest",
    "RAGIngestResult",
    "RAGSource",
    "UserProfile",
]
