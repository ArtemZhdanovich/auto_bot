"""Схемы контракта RAG-сервиса (план внедрения RAG, п. 6.3)."""

from typing import Any

from pydantic import BaseModel, Field


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
    confidence: float = 0.0
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


__all__ = ["RAGAnswer", "RAGAskRequest", "RAGIngestResult", "RAGSource"]
