"""Маппинг доменных событий в конверт outbox/брокера."""

from __future__ import annotations

import dataclasses
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from app.domain.shared.events import DomainEvent

# Явная карта маршрутизации: имя класса не должно протекать в топологию.
_EVENT_ROUTING: dict[str, str] = {
    "LeadCreated": "lead.created",
    "LeadClaimed": "lead.claimed",
    "PaymentSucceeded": "payment.succeeded",
}


def routing_key_for(event: DomainEvent) -> str:
    """Routing key для события (по типу, с безопасным fallback)."""
    return _EVENT_ROUTING.get(type(event).__name__, "domain.event")


def _jsonable(value: Any) -> Any:
    """Привести значение к JSON-совместимому виду."""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


def event_to_dict(event: DomainEvent) -> dict[str, Any]:
    """Сериализовать событие в конверт для брокера.

    Returns:
        `{"event_id", "event_type", "occurred_at", "payload"}`.
    """
    payload: dict[str, Any] = {}
    for field in dataclasses.fields(event):
        if field.name in {"event_id", "occurred_at"}:
            continue
        payload[field.name] = _jsonable(getattr(event, field.name))
    return {
        "event_id": str(event.event_id),
        "event_type": type(event).__name__,
        "occurred_at": event.occurred_at.isoformat(),
        "payload": payload,
    }
