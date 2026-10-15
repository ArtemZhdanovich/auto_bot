"""API Gateway (профиль micro): единая точка входа `/api/*`.

Маршрутизирует запросы в доменные сервисы (identity / catalog / billing /
partners / dialog / admin) по таблице `routing.ROUTE_RULES`, пробрасывая
метод, заголовки, тело и query. Секреты (X-Internal-Token, Authorization,
X-API-Key) проходят сквозь него без изменений — аутентификация остаётся
в доменных сервисах.

В docker-compose контейнер gateway получает сетевой алиас `backend`,
поэтому бот (`APP_BASE_URL=http://backend:8000`) и nginx Mini App
(`proxy_pass http://backend:8000`) работают в режиме micro без правок.
"""

import asyncio
import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from app.core.config import get_settings
from services.gateway.routing import SERVICE_NAMES, resolve_service

logger = logging.getLogger(__name__)

SERVICE_NAME = "gateway"

# Заголовки, которые нельзя слепо пробрасывать (пересчитываются httpx/сервером)
_HOP_BY_HOP = frozenset(
    {
        "host",
        "content-length",
        "connection",
        "keep-alive",
        "transfer-encoding",
        "te",
        "trailer",
        "upgrade",
    }
)


def service_urls() -> dict[str, str]:
    """Базовые URL доменных сервисов из настроек (env: SVC_*_URL)."""
    s = get_settings()
    return {
        "identity": s.svc_identity_url,
        "catalog": s.svc_catalog_url,
        "billing": s.svc_billing_url,
        "partners": s.svc_partners_url,
        "dialog": s.svc_dialog_url,
        "admin": s.svc_admin_url,
    }


def create_app(transport: httpx.AsyncBaseTransport | None = None) -> FastAPI:
    """Фабрика gateway. `transport` — точка инъекции MockTransport в тестах."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        logging.basicConfig(level=get_settings().log_level)
        yield
        # Клиент создаётся в create_app (а не здесь): ASGITransport в тестах
        # не запускает lifespan, но прокси должен работать сразу.
        await app.state.client.aclose()

    app = FastAPI(
        title="auto_bot gateway",
        version="0.1.0",
        description="Маршрутизация /api/* в доменные микросервисы (профиль micro)",
        lifespan=lifespan,
    )
    app.state.client = httpx.AsyncClient(
        timeout=get_settings().gateway_timeout_sec, transport=transport
    )

    @app.get("/health", tags=["system"])
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": SERVICE_NAME, "env": get_settings().app_env}

    @app.get("/health/detailed", tags=["system"])
    async def health_detailed() -> JSONResponse:
        """Пинг всех доменных сервисов; 503, если хоть один недоступен."""
        urls = service_urls()

        async def ping(name: str, base: str) -> tuple[str, bool]:
            try:
                resp = await app.state.client.get(f"{base}/health")
                return name, resp.status_code == 200
            except httpx.HTTPError:
                return name, False

        results = await asyncio.gather(*(ping(n, u) for n, u in urls.items()))
        components = dict(results)
        all_ok = all(components.values())
        return JSONResponse(
            status_code=200 if all_ok else 503,
            content={
                "status": "ok" if all_ok else "degraded",
                "service": SERVICE_NAME,
                "components": components,
            },
        )

    async def _forward(target: str, request: Request) -> Response:
        """Отправить запрос в `target` (таблица маршрутов уже разрешила имя)."""
        base = service_urls()[target]
        headers = {
            k: v for k, v in request.headers.items() if k.lower() not in _HOP_BY_HOP
        }
        body = await request.body()
        try:
            upstream = await app.state.client.request(
                request.method,
                f"{base}{request.url.path}",
                params=request.url.query,  # raw query string, без потери дублей
                headers=headers,
                content=body,
            )
        except httpx.HTTPError as exc:
            logger.warning("gateway_upstream_failed: %s %s", target, exc)
            return JSONResponse(
                status_code=502,
                content={"detail": f"service '{target}' unavailable"},
            )
        return Response(
            content=upstream.content,
            status_code=upstream.status_code,
            media_type=upstream.headers.get("content-type"),
        )

    @app.api_route(
        "/api/{path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
    )
    async def proxy(path: str, request: Request) -> Response:
        """Проксировать запрос в сервис по таблице маршрутов."""
        target = resolve_service(request.url.path)
        if target is None or target not in SERVICE_NAMES:
            return JSONResponse(
                status_code=404,
                content={"detail": f"no route for {request.url.path}"},
            )
        return await _forward(target, request)

    @app.api_route("/metrics", methods=["GET"], tags=["system"])
    async def metrics_proxy(request: Request) -> Response:
        """GET /metrics (мониторинг, X-Internal-Token) — роут вне /api/*:
        живёт в admin-домене (app/api/metrics.py), пробрасывается напрямую."""
        return await _forward("admin", request)

    return app


app = create_app()
