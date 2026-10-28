"""AI-отчёт истории лота (12 §1): факты ДТП из чека + LLM-вывод + кэш.

Формат ДТП-строк — канонический вывод адаптера encar (record/open →
summary): счётчик «история ДТП: N страховых случаев (…)» и строки
«ДТП YYYY-MM-DD — выплата N 원»; факты живут в summary отчёта осмотра.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.db.models import InspectionReport

pytestmark = pytest.mark.asyncio

LLM_TEXT = (
    "Итог: два страховых случая в истории — стоит запросить детали "
    "ремонта и сверить зоны с чеком осмотра перед покупкой."
)


class _FakeLLM:
    """LLM-заглушка: возвращает абзац-вывод для отчёта истории."""

    async def complete(self, messages):
        return SimpleNamespace(text=LLM_TEXT, tool_calls=[])


class _FailingLLM:
    """LLLM-заглушка: любой вызов падает (фолбэк без абзаца)."""

    async def complete(self, messages):
        raise RuntimeError("llm down")


@pytest.fixture
def fake_llm(monkeypatch):
    """Подменяет LLM-фабрику на `_FakeLLM` (без сети)."""
    monkeypatch.setattr(
        "app.di.providers.adapters._llm", lambda: _FakeLLM()
    )


@pytest.fixture
def failing_llm(monkeypatch):
    """Подменяет LLM-фабрику на падающую заглушку."""
    monkeypatch.setattr(
        "app.di.providers.adapters._llm", lambda: _FailingLLM()
    )


async def _report_with_accidents(
    session: AsyncSession, listing_id: int
) -> InspectionReport:
    """Чек осмотра с фактами record/open в summary (формат encar)."""
    report = InspectionReport(
        listing_id=listing_id,
        source="encar",
        inspected_at=datetime(2026, 10, 1, tzinfo=UTC),
        grade="good",
        summary=(
            "Диагностика Encar: пройдена · чек осмотра: отмечены ДТП · "
            "история ДТП: 2 страховых случаев (по этому авто: 1, "
            "по вине других: 1) · ДТП 2021-05-01 — выплата 830 000 원 · "
            "ДТП 2024-02-02 — выплата 306 000 · смен владельцев: 2 · "
            "первая регистрация: 2021-03-10"
        ),
        marks=[{"zone": "hood", "type": "replace", "note": "교환"}],
        items=[
            {"key": "engine", "status": "ok"},
            {"key": "brakes", "status": "warn", "note": "brakes — warn"},
        ],
    )
    session.add(report)
    await session.flush()
    return report


async def test_history_report_facts_and_llm(
    client: AsyncClient,
    auth_headers: dict,
    session: AsyncSession,
    listing,
    fake_llm,
):
    """Факты ДТП из summary + LLM-абзац: 200 со сводкой (12 §1)."""
    await _report_with_accidents(session, listing.id)

    resp = await client.get(
        f"/api/listings/{listing.id}/history-report", headers=auth_headers
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["listing_id"] == listing.id
    assert data["llm_summary"] == LLM_TEXT

    accidents = data["facts"]["accidents"]
    assert accidents["count"] == 2
    assert accidents["by_this_car"] == 1 and accidents["by_others"] == 1
    # Последнее ДТП = максимальная дата из строк «ДТП …».
    assert accidents["last"]["date"] == "2024-02-02"
    assert "306 000" in accidents["last"]["description"]

    facts = data["facts"]
    assert facts["inspection_accidents"] is True
    assert facts["owners_count"] == 2
    assert facts["first_registration"] == "2021-03-10"
    # Пробег подтверждён: чек осмотра есть.
    assert facts["mileage_confirmed"] is True
    assert facts["inspection"]["grade"] == "good"
    assert facts["inspection"]["issues"] == [
        {"key": "brakes", "status": "warn", "note": "brakes — warn"}
    ]
    assert facts["car"]["make"] == "Kia"
    assert data["generated_at"]


async def test_history_report_llm_failure_facts_only(
    client: AsyncClient,
    auth_headers: dict,
    session: AsyncSession,
    listing,
    failing_llm,
):
    """Сбой LLM → отчёт без абзаца, факты остаются (12 §1б)."""
    await _report_with_accidents(session, listing.id)

    resp = await client.get(
        f"/api/listings/{listing.id}/history-report", headers=auth_headers
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["llm_summary"] is None
    assert data["facts"]["accidents"]["count"] == 2


async def test_history_report_cached_without_llm(
    client: AsyncClient,
    auth_headers: dict,
    session: AsyncSession,
    listing,
    fake_llm,
    monkeypatch,
):
    """Второй вызов берёт готовый отчёт из Redis — LLM не нужен."""
    await _report_with_accidents(session, listing.id)

    first = await client.get(
        f"/api/listings/{listing.id}/history-report", headers=auth_headers
    )
    assert first.status_code == 200
    assert first.json()["llm_summary"] == LLM_TEXT

    # LLM «ложится» после первого запроса: кэш 24ч должен отдать отчёт.
    monkeypatch.setattr(
        "app.di.providers.adapters._llm", lambda: _FailingLLM()
    )
    second = await client.get(
        f"/api/listings/{listing.id}/history-report", headers=auth_headers
    )
    assert second.status_code == 200
    assert second.json()["llm_summary"] == LLM_TEXT


async def test_history_report_404_without_report(
    client: AsyncClient, auth_headers: dict, listing
):
    """Нет чека и owner-истории → 404 no_history_data (12 §1)."""
    resp = await client.get(
        f"/api/listings/{listing.id}/history-report", headers=auth_headers
    )
    assert resp.status_code == 404
    assert "no_history_data" in resp.json()["detail"]


async def test_history_report_guest_403(
    client: AsyncClient, guest_headers: dict, session: AsyncSession, listing
):
    """Гость получает 403 (как AI-анализ, 06 §5)."""
    await _report_with_accidents(session, listing.id)
    resp = await client.get(
        f"/api/listings/{listing.id}/history-report", headers=guest_headers
    )
    assert resp.status_code == 403
    assert "history_guest_forbidden" in resp.json()["detail"]
