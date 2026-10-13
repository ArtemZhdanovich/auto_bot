"""Таблица маршрутов gateway: префикс пути API → имя доменного сервиса.

Порядок правил значим: более специфичные проверяются раньше
(`/api/users/{id}/subscription` → billing до `/api/users` → identity).
`{param}` — ровно один непустой сегмент пути.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class RouteRule:
    prefix: str
    service: str


#: Первый совпавший префикс побеждает — специфичность важнее алфавита.
ROUTE_RULES: tuple[RouteRule, ...] = (
    RouteRule("/api/users/{user_id}/subscription", "billing"),
    RouteRule("/api/users", "identity"),
    RouteRule("/api/auth", "identity"),
    RouteRule("/api/me", "identity"),
    RouteRule("/api/listings", "catalog"),
    RouteRule("/api/favorites", "catalog"),
    RouteRule("/api/filters", "catalog"),
    RouteRule("/api/notifications", "catalog"),
    RouteRule("/api/payments", "billing"),
    RouteRule("/api/subscription", "billing"),
    RouteRule("/api/partner", "partners"),
    RouteRule("/api/admin/partners", "partners"),
    RouteRule("/api/leads", "partners"),
    RouteRule("/api/dialog", "dialog"),
    RouteRule("/api/ai", "dialog"),
    RouteRule("/api/admin", "admin"),
)

#: Имена сервисов, в которые gateway умеет проксировать.
SERVICE_NAMES: frozenset[str] = frozenset(rule.service for rule in ROUTE_RULES)


def _match_prefix(rule: str, path: str) -> bool:
    """`rule` совпадает с началом `path`; `{param}` — один сегмент без '/'."""
    rule_parts = rule.strip("/").split("/")
    path_parts = path.strip("/").split("/")
    if len(path_parts) < len(rule_parts):
        return False
    for rule_part, path_part in zip(rule_parts, path_parts):
        if rule_part.startswith("{") and rule_part.endswith("}"):
            if not path_part:  # пустой сегмент не матчится
                return False
        elif rule_part != path_part:
            return False
    return True


def resolve_service(path: str) -> str | None:
    """Имя сервиса для пути или None (вне /api/* или неизвестный маршрут)."""
    if not path.startswith("/api/"):
        return None
    for rule in ROUTE_RULES:
        if _match_prefix(rule.prefix, path):
            return rule.service
    return None
