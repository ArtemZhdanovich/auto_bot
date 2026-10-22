"""partners-service (8104): партнёрский контур CPL и лиды.

Домены: /api/partner/* (X-API-Key: ЛК, пул лидов, claim, фидбек, биллинг),
/api/admin/partners/* (X-Internal-Token: партнёры, ключи, баланс),
/api/leads (internal: создание лидов ботом/агентом, статусы менеджера).
"""

from fastapi import FastAPI

from app.transport.http.routers import leads, partners
from services._base import create_domain_service

SERVICE_NAME = "partners"


def create_app() -> FastAPI:
    return create_domain_service(
        SERVICE_NAME,
        "0.1.0",
        [partners.admin_router, partners.router, leads.router],
    )


app = create_app()
