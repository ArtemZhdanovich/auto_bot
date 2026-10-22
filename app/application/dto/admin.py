"""DTO админ-панели: контракт источника и проверки безопасности.

SSRF-проверки и префиксы `credentials_ref` — чистые функции без зависимостей,
поэтому их использует сценарий, а транспорт только отдаёт результат клиенту.
"""

from __future__ import annotations

import ipaddress
from typing import Any, Literal
from urllib.parse import urlparse

from dataclasses import dataclass, field
from pydantic import BaseModel, Field, ValidationInfo, field_validator

# Префиксы credentials_ref, разрешённые для назначения источнику.
ALLOWED_CREDENTIAL_PREFIXES: tuple[str, ...] = (
    "ADAPTER_",
    "SOURCE_",
    "SECRET_",
    "CREDENTIALS_",
)
# Приватные/служебные сети — запрещены как base_url (SSRF).
FORBIDDEN_HOSTNAMES: frozenset[str] = frozenset(
    {"localhost", "metadata.google.internal"}
)
# Поля config, которые обязаны указывать на внешний http(s)-хост.
SOURCE_URL_KEYS: tuple[str, ...] = ("base_url", "detail_url_template", "list_url")
SOURCE_NAME_PATTERN = r"^[a-z0-9][a-z0-9_-]{0,63}$"
DEFAULT_RETRY_POLICY: dict[str, Any] = {"max": 3, "backoff": "exponential"}


def is_safe_external_url(url: str) -> bool:
    """Проверяет, что URL — http(s) и не указывает на приватный/loopback хост.

    Защита от SSRF: admin не должен мочь направить адаптер на
    `169.254.169.254` (AWS metadata), `10.0.0.1` (внутренняя сеть),
    `localhost` (внутренние сервисы).

    Caveat: DNS rebinding не покрывается (имя может резолвиться в
    приватный IP). Для полной защиты нужен allowlist хостов по
    adapter_class.

    Args:
        url: Значение `config.base_url` (или другого URL-поля).

    Returns:
        `True`, если URL безопасен для внешнего запроса.
    """
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    host = (parsed.hostname or "").lower()
    if not host:
        return False
    if host in FORBIDDEN_HOSTNAMES:
        return False
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return True  # не IP — доменное имя, пропускаем
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
    )


def is_allowed_credentials_ref(ref: str | None) -> bool:
    """Проверяет префикс `credentials_ref` (defense-in-depth к EnvSecretStore).

    Args:
        ref: Имя env-переменной или `None`.

    Returns:
        `True`, если префикс разрешён (пустое значение тоже допустимо).
    """
    if not ref:
        return True
    return ref.startswith(ALLOWED_CREDENTIAL_PREFIXES)


class SourceConfigDTO(BaseModel):
    """Источник в API-контракте админки (тело запроса и ответ)."""

    name: str
    adapter_class: str
    kind: Literal["api", "parser", "hybrid"]
    enabled: bool = True
    priority: int = 100
    schedule_cron: str | None = None
    rate_limit_per_min: int = 60
    max_concurrency: int = 3
    timeout_sec: int = 30
    retry_policy: dict[str, Any] = Field(
        default_factory=lambda: dict(DEFAULT_RETRY_POLICY)
    )
    credentials_ref: str | None = None
    capabilities: dict[str, Any] = Field(default_factory=dict)
    config: dict[str, Any] = Field(default_factory=dict)

    @field_validator("retry_policy", "capabilities", "config", mode="before")
    @classmethod
    def _none_to_default(cls, value: Any, info: ValidationInfo) -> Any:
        """NULL-колонки БД → дефолты (иначе GET /sources падает с 500)."""
        if value is None:
            if info.field_name == "retry_policy":
                return dict(DEFAULT_RETRY_POLICY)
            return {}
        return value


@dataclass(frozen=True, slots=True)
class SourceState:
    """Словари источника, нужные для слияния при обновлении."""

    retry_policy: dict[str, Any] = field(default_factory=dict)
    capabilities: dict[str, Any] = field(default_factory=dict)
    config: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class LeadsQuery:
    """Фильтры списка лидов для админ-панели."""

    status: str | None = None
    partner_id: int | None = None
    limit: int = 50
    offset: int = 0
