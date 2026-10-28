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
    # Чаты/рынок (редизайн части 5-6): партнёрские зеркала — специфичные
    # правила ОБЯЗАНЫ стоять до /api/partner → partners. Seller-чат живёт
    # формой /api/chats/seller/{listing_id} — накрывается правилом /api/chats.
    # /api/partner/market (06 §2) — пакеты размещений и лоты партнёра:
    # живут в chat-сервисе вместе с market.partner_router.
    RouteRule("/api/partner/offers", "chat"),
    RouteRule("/api/partner/chats", "chat"),
    RouteRule("/api/partner/market", "chat"),
    # Чеки осмотра и история лотов рынка — домен chat (07 §3)
    RouteRule("/api/partner/listings", "chat"),
    RouteRule("/api/chats", "chat"),
    RouteRule("/api/market", "chat"),
    RouteRule("/api/listings", "catalog"),
    RouteRule("/api/favorites", "catalog"),
    RouteRule("/api/filters", "catalog"),
    RouteRule("/api/notifications", "catalog"),
    RouteRule("/api/calc", "catalog"),
    # Внутренний канал бота (analysis.bot_router: X-Internal-Token);
    # веб-часть анализа /api/listings/{id}/analysis уходит правилом
    # /api/listings выше — тоже в catalog.
    RouteRule("/api/bot", "catalog"),
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
