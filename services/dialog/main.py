"""dialog-service (8105): AI-диалог и AI-поиск.

Домены: /api/dialog/* (internal, ходит бот) и /api/ai/* (JWT, Mini App).
Единственный сервис, требующий LLM-провайдера (GLM/Anthropic/Ollama)
и опционально RAG: вынесен отдельно, чтобы LLM-нагрузка/отказы не влияли
на остальные контуры.
"""

from fastapi import FastAPI

from app.transport.http.routers import ai_search, dialog
from services._base import create_domain_service

SERVICE_NAME = "dialog"


def create_app() -> FastAPI:
    return create_domain_service(
        SERVICE_NAME,
        "0.1.0",
        [dialog.router, ai_search.router],
    )


app = create_app()
