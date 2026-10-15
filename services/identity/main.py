"""identity-service (8101): авторизация и профили.

Домены: /api/auth (initData/веб/JWT + refresh), /api/users (upsert бота),
/api/me (профиль, онбординг).
"""

from fastapi import FastAPI

from app.api import auth, me, users
from services._base import create_domain_service

SERVICE_NAME = "identity"


def create_app() -> FastAPI:
    return create_domain_service(
        SERVICE_NAME,
        "0.1.0",
        [auth.router, users.router, me.router],
    )


app = create_app()
