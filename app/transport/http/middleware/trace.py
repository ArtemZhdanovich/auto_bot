"""Сквозной `trace_id` для логов и ответов (фаза 1, наблюдаемость).

Идея: один идентификатор на HTTP-запрос, доступный из любого слоя через
`contextvars`, без протаскивания параметра через все функции.

- Входящий `X-Request-ID` переиспользуется (удобно для gateway/бота).
- Если заголовка нет — генерируется новый.
- Значение возвращается клиенту в `X-Request-ID`, чтобы можно было
  сопоставить жалобу пользователя с логами.
"""

from __future__ import annotations

import logging
import uuid
from contextvars import ContextVar

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

trace_id_var: ContextVar[str] = ContextVar("trace_id", default="")

_MAX_TRACE_ID_LEN = 64


def current_trace_id() -> str:
    """Текущий `trace_id` или пустая строка вне запроса."""
    return trace_id_var.get()


class TraceIdMiddleware(BaseHTTPMiddleware):
    """Проставляет `trace_id` в контекст и в ответ."""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        incoming = request.headers.get("X-Request-ID", "").strip()
        # Не доверяем произвольной длине из заголовка: 64 символа достаточно
        # для UUID/hex, всё длиннее — мусор и риск раздуть логи.
        trace_id = incoming[:_MAX_TRACE_ID_LEN] if incoming else uuid.uuid4().hex
        token = trace_id_var.set(trace_id)
        try:
            response = await call_next(request)
        finally:
            trace_id_var.reset(token)
        response.headers["X-Request-ID"] = trace_id
        return response


class TraceIdLogFilter(logging.Filter):
    """Добавляет `trace_id` в каждую запись лога."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.trace_id = trace_id_var.get() or "-"
        return True


def install_trace_logging() -> None:
    """Включает формат с `trace_id` на корневом логгере (идемпотентно)."""
    root = logging.getLogger()
    for handler in root.handlers:
        if any(isinstance(f, TraceIdLogFilter) for f in handler.filters):
            continue
        handler.addFilter(TraceIdLogFilter())
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)s [%(trace_id)s] %(name)s: %(message)s"
            )
        )
