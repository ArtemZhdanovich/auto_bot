"""Юнит-тесты таблицы маршрутов gateway (services/gateway/routing.py)."""

import pytest

from services.gateway.routing import ROUTE_RULES, SERVICE_NAMES, resolve_service


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        # --- identity ---
        ("/api/auth/telegram", "identity"),
        ("/api/auth/refresh", "identity"),
        ("/api/auth/register", "identity"),
        ("/api/users/upsert", "identity"),
        ("/api/users/123", "identity"),
        ("/api/me", "identity"),
        ("/api/me/onboarded", "identity"),
        # --- catalog ---
        ("/api/listings", "catalog"),
        ("/api/listings/42", "catalog"),
        ("/api/favorites", "catalog"),
        ("/api/filters/7", "catalog"),
        ("/api/notifications/settings", "catalog"),
        ("/api/notifications/scan", "catalog"),
        # --- billing ---
        ("/api/payments/stars", "billing"),
        ("/api/payments/prepare", "billing"),
        ("/api/payments/success", "billing"),
        ("/api/subscription", "billing"),
        # --- partners ---
        ("/api/partner/me", "partners"),
        ("/api/partner/leads/5/claim", "partners"),
        ("/api/partner/leads/batch", "partners"),
        ("/api/admin/partners", "partners"),
        ("/api/admin/partners/3/billing", "partners"),
        ("/api/admin/partners/3/rotate", "partners"),
        ("/api/leads", "partners"),
        ("/api/leads/9", "partners"),
        # --- dialog ---
        ("/api/dialog/message", "dialog"),
        ("/api/dialog/finalize", "dialog"),
        ("/api/ai/finalize", "dialog"),
        ("/api/ai/message", "dialog"),
        # --- admin ---
        ("/api/admin/sources/encar/fetch", "admin"),
        ("/api/admin/analytics/funnel", "admin"),
        ("/api/admin/analytics/retention", "admin"),
        ("/api/admin/listings/1/raw", "admin"),
        ("/api/admin/assistant/chat", "admin"),
        ("/api/admin/adapters", "admin"),
    ],
)
def test_resolve_known_routes(path: str, expected: str):
    assert resolve_service(path) == expected


@pytest.mark.parametrize(
    "path",
    [
        "/health",
        "/health/detailed",
        "/metrics",
        "/docs",
        "/favicon.ico",
        "/api",  # голый префикс без сегмента
        "/api/",  # то же со слэшем
        "/apix/listings",  # начинается с /api, но не префикс /api/
        "/api/unknown",  # сегмент вне таблицы
        "/api/adminx",  # не префикс /api/admin
    ],
)
def test_resolve_unknown_routes(path: str):
    assert resolve_service(path) is None


def test_specificity_users_subscription_beats_users():
    """/api/users/{id}/subscription уходит в billing, остальные /api/users — identity."""
    assert resolve_service("/api/users/5/subscription") == "billing"
    assert resolve_service("/api/users/5") == "identity"
    assert resolve_service("/api/users/upsert") == "identity"


def test_param_segment_requires_non_empty():
    # Пустой сегмент не матчится с {user_id}: срабатывает правило /api/users
    assert resolve_service("/api/users//subscription") == "identity"


def test_admin_partners_beats_admin():
    assert resolve_service("/api/admin/partners") == "partners"
    assert resolve_service("/api/admin/sources") == "admin"


def test_rules_target_known_services():
    assert {rule.service for rule in ROUTE_RULES} == set(SERVICE_NAMES)
    assert SERVICE_NAMES == {
        "identity",
        "catalog",
        "billing",
        "partners",
        "dialog",
        "admin",
    }


def test_rules_are_ordered_specific_first():
    """Специфичные правила обязаны стоять раньше общих префиксов."""
    order = [rule.prefix for rule in ROUTE_RULES]
    assert order.index("/api/users/{user_id}/subscription") < order.index("/api/users")
    assert order.index("/api/admin/partners") < order.index("/api/admin")
