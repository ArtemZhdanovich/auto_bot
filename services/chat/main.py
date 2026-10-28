"""chat-service (8107): чаты, поддержка и внутренний рынок (редизайн ч.5).

Домены: /api/chats (треды/сообщения/ws-токены/subscribe-proxy/превью),
/api/market (витрина) и партнёрские зеркала /api/partner/offers,
/api/partner/chats (X-API-Key; остальной /api/partner — partners-сервис).
Gateway заводит сюда же /api/listings/{id}/seller-chat (шаблонное правило),
но эндпоинт использует форму /api/chats/seller/{listing_id}.
"""

from fastapi import FastAPI

from app.transport.http.routers import chats, market
from services._base import create_domain_service

SERVICE_NAME = "chat"


def create_app() -> FastAPI:
    return create_domain_service(
        SERVICE_NAME,
        "0.1.0",
        [
            chats.router,
            market.market_router,
            market.partner_router,
        ],
    )


app = create_app()
