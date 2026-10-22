"""Архитектурные тесты: направление зависимостей между слоями."""

from __future__ import annotations

import pathlib

APP = pathlib.Path("app")


def _text(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def test_domain_and_application_have_no_outward_imports() -> None:
    """domain/application не знают о infra/transport/di."""
    forbidden = (
        "app.infrastructure",
        "app.transport",
        "app.di",
        "app.main_api",
    )
    violations: list[str] = []
    for layer in ("domain", "application"):
        for path in (APP / layer).rglob("*.py"):
            text = _text(path)
            for marker in forbidden:
                if marker in text:
                    violations.append(f"{path}: {marker}")
    assert violations == []


def test_favorites_transport_is_decoupled() -> None:
    """Эталонный контроллер не импортирует домен и инфраструктуру."""
    text = _text(APP / "transport/http/routers/favorites.py")
    assert "app.domain" not in text
    assert "app.infrastructure" not in text
    assert "SessionDep" not in text


def test_application_ports_are_protocols() -> None:
    """Порты объявлены через Protocol, а не конкретные клиенты."""
    shared = _text(APP / "application/shared/ports.py")
    for name in ("CachePort", "LLMPort", "ClockPort", "UnitOfWork"):
        assert f"class {name}(Protocol)" in shared
    service_ports = "\n".join(
        _text(APP / "application" / svc / "ports.py")
        for svc in ("identity", "catalog", "billing", "partners", "dialog", "admin")
    )
    for name in (
        "UserRepository",
        "ListingReadRepository",
        "PaymentRepository",
        "PartnerRepository",
        "DialogAgentPort",
        "MetricsPort",
    ):
        assert f"class {name}(Protocol)" in service_ports


def test_application_has_no_orm_or_http_imports() -> None:
    """В application нет SQLAlchemy/FastAPI/aiogram."""
    forbidden = ("sqlalchemy", "fastapi", "aiogram", "faststream", "httpx")
    violations: list[str] = []
    for path in (APP / "application").rglob("*.py"):
        text = _text(path)
        for marker in forbidden:
            if f"import {marker}" in text or f"from {marker}" in text:
                violations.append(f"{path}: {marker}")
    assert violations == []
