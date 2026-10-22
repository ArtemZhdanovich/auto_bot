"""admin-service (8106): админ-контур и аналитика.

Домены: /api/admin/* (X-Internal-Token) — источники и ручной парсинг,
health источников, лиды (read-only), AI-ассистент, presigned raw,
воронка/retention, GET /metrics (агрегаты мониторинга — общий роутер
с монолитом, gateway проксирует /metrics сюда). Партнёрские админ-роуты
(/api/admin/partners/*) живут в partners-service.
"""

from fastapi import FastAPI

from app.transport.http.routers import admin, metrics
from services._base import create_domain_service

SERVICE_NAME = "admin"


def create_app() -> FastAPI:
    return create_domain_service(
        SERVICE_NAME,
        "0.1.0",
        [admin.router, metrics.router],
    )


app = create_app()
