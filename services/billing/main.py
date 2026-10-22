"""billing-service (8103): подписки и Telegram Stars.

Домены: /api/subscription (статус, лимиты, цены), /api/payments/*
(инвойсы XTR, internal prepare/success для бота),
/api/users/{id}/subscription (internal, тариф для бота).
"""

from fastapi import FastAPI

from app.transport.http.routers import payments
from services._base import create_domain_service

SERVICE_NAME = "billing"


def create_app() -> FastAPI:
    return create_domain_service(
        SERVICE_NAME,
        "0.1.0",
        [payments.router],
    )


app = create_app()
