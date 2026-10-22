"""API AI-анализа: JWT POST (квота free 1/нед), GET кэша, internal-роутер бота.

IDOR: web-канал не принимает `user_id` из тела — владелец разбора всегда
берётся из токена (проверяется отдельным тестом).
"""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dishka import Provider, Scope, provide

from app.application.analysis.use_cases import RunAnalysis
from conftest import install_test_container
from app.config import get_settings
from app.infrastructure.db.models import PriceAnalysis, User
from app.infrastructure.security.tokens import create_access_token
from app.main_api import app as fastapi_app


class _FakeLLM:
    """LLM-заглушка: всегда отдаёт валидный JSON разбора цены."""

    async def complete(self, messages):
        return SimpleNamespace(
            text=(
                '{"verdict": "deal", "fair_min": 29000, "fair_max": 33000, '
                '"summary": "Цена ниже рынка", "factors": ["медиана 31000"]}'
            ),
            tool_calls=[],
        )

    async def aclose(self) -> None:
        return None


@pytest.fixture
def fake_llm(monkeypatch):
    """Подменяет LLM-фабрику на `_FakeLLM` (без сети)."""
    monkeypatch.setattr(
        "app.di.providers.adapters._llm", lambda: _FakeLLM()
    )


@pytest.fixture
def internal_headers(internal_token) -> dict[str, str]:
    """Заголовки с internal-токеном (значения из настроек приложения)."""
    return {"X-Internal-Token": internal_token}


def _email_user_headers(user: User) -> dict[str, str]:
    """Bearer-заголовок для пользователя с email."""
    token = create_access_token(user.telegram_id, get_settings())
    return {"Authorization": f"Bearer {token}"}


async def _add_analysis(
    session: AsyncSession, listing_id: int, user_id: int, verdict: str = "fair"
) -> None:
    """Кладёт в БД прошлый разбор (для проверки квоты)."""
    session.add(
        PriceAnalysis(listing_id=listing_id, user_id=user_id, verdict=verdict)
    )
    await session.commit()


# ---------- web: POST ----------


@pytest.mark.asyncio
async def test_post_analysis_201_and_saved(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session: AsyncSession,
    user: User,
    listing,
    fake_llm,
):
    resp = await client.post(
        f"/api/listings/{listing.id}/analysis", headers=auth_headers
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["verdict"] == "deal"
    assert data["fair_min"] == 29000 and data["fair_max"] == 33000
    assert data["source"] == "ai"

    rows = (
        (
            await session.execute(
                select(PriceAnalysis).where(PriceAnalysis.listing_id == listing.id)
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].user_id == user.telegram_id


@pytest.mark.asyncio
async def test_post_analysis_free_quota_403(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session: AsyncSession,
    user: User,
    listing,
    fake_llm,
):
    """Free: второй разбор той же машины в неделю → 403 quota_exceeded."""
    await _add_analysis(session, listing.id, user.telegram_id)
    resp = await client.post(
        f"/api/listings/{listing.id}/analysis", headers=auth_headers
    )
    assert resp.status_code == 403
    assert "quota_exceeded" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_post_analysis_pro_unlimited(
    client: AsyncClient,
    session: AsyncSession,
    pro_user: User,
    listing,
    fake_llm,
):
    """Pro: квота не проверяется вовсе — даже при наличии прошлого разбора."""
    await _add_analysis(session, listing.id, pro_user.telegram_id)
    user_id = pro_user.telegram_id
    token = create_access_token(user_id, get_settings())
    resp = await client.post(
        f"/api/listings/{listing.id}/analysis",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
async def test_post_analysis_missing_listing_404(
    client: AsyncClient,
    auth_headers: dict[str, str],
    user: User,
    fake_llm,
):
    resp = await client.post(
        "/api/listings/999999/analysis", headers=auth_headers
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_create_analysis_requires_jwt(client: AsyncClient, listing):
    r = await client.post(f"/api/listings/{listing.id}/analysis")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_listing_id_must_be_positive(
    client: AsyncClient, auth_headers: dict[str, str], user: User
):
    r = await client.post("/api/listings/0/analysis", headers=auth_headers)
    assert r.status_code == 422  # Path(ge=1)


@pytest.mark.asyncio
async def test_llm_error_does_not_leak_reason(
    client: AsyncClient,
    auth_headers: dict[str, str],
    user: User,
    listing,
    monkeypatch,
):
    from app.infrastructure.ai.llm_client import LLMError

    def failing(*a, **kw):
        raise LLMError("GLM_API_KEY is not set")

    monkeypatch.setattr(
        "app.di.providers.adapters.get_cached_llm_client", failing
    )
    r = await client.post(
        f"/api/listings/{listing.id}/analysis", headers=auth_headers
    )
    assert r.status_code == 503
    assert "GLM_API_KEY" not in r.json()["detail"]


@pytest.mark.asyncio
async def test_quota_exceeded_returns_403(
    client: AsyncClient,
    auth_headers: dict[str, str],
    user: User,
    listing,
    fake_llm,
    monkeypatch,
):
    """Доменный `QuotaExceeded` из сценария → 403 quota_exceeded."""
    from app.domain.pricing.rules import QuotaExceeded

    class _Denied:
        def __init__(self, *a, **kw) -> None:
            pass

        async def execute(self, command):
            raise QuotaExceeded()

    monkeypatch.setattr(
        "app.application.analysis.use_cases.AnalyzePrice", _Denied
    )
    r = await client.post(
        f"/api/listings/{listing.id}/analysis", headers=auth_headers
    )
    assert r.status_code == 403
    assert "quota_exceeded" in r.json()["detail"]


@pytest.mark.asyncio
async def test_invalid_payload_returns_502(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session: AsyncSession,
    user: User,
    listing,
    monkeypatch,
):
    """Сценарий вернул payload с невалидным created_at → 502, не 500."""

    class _BadResult:
        def to_payload(self) -> dict:
            return {
                "verdict": "ok",
                "price": 1.0,
                "currency": "KRW",
                "created_at": datetime.now(),  # datetime, а не str
            }

    class _BadUseCase:
        async def execute(self, command):
            return _BadResult()

    class _BadRunAnalysisProvider(Provider):
        """Подмена сценария: execute возвращает невалидный payload."""

        @provide(scope=Scope.REQUEST)
        def bad(self) -> RunAnalysis:
            return _BadUseCase()

    _prev = fastapi_app.state.dishka_container
    _container = install_test_container(
        fastapi_app, session=session, extra=(_BadRunAnalysisProvider(),)
    )
    try:
        r = await client.post(
            f"/api/listings/{listing.id}/analysis", headers=auth_headers
        )
    finally:
        fastapi_app.state.dishka_container = _prev
        await _container.close()
    assert r.status_code == 502


# ---------- IDOR: канал не принимает чужой user_id ----------


@pytest.mark.asyncio
async def test_web_analysis_ignores_user_id_in_body(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session: AsyncSession,
    user: User,
    listing,
    fake_llm,
):
    """`user_id` из тела игнорируется: разбор принадлежит владельцу токена."""
    resp = await client.post(
        f"/api/listings/{listing.id}/analysis",
        json={"user_id": user.telegram_id + 1},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    rows = (
        (
            await session.execute(
                select(PriceAnalysis).where(PriceAnalysis.listing_id == listing.id)
            )
        )
        .scalars()
        .all()
    )
    assert [row.user_id for row in rows] == [user.telegram_id]


# ---------- гостям AI-анализ закрыт ----------


@pytest.mark.asyncio
async def test_post_analysis_guest_403(
    client: AsyncClient,
    guest_headers: dict[str, str],
    session: AsyncSession,
    listing,
    fake_llm,
):
    """Гость (id < 0, без email) получает 403 — анализ только авторизированным."""
    resp = await client.post(
        f"/api/listings/{listing.id}/analysis", headers=guest_headers
    )
    assert resp.status_code == 403
    assert "guest_forbidden" in resp.json()["detail"]
    # LLM даже не вызывался: разбор не создан
    rows = (
        (
            await session.execute(
                select(PriceAnalysis).where(PriceAnalysis.listing_id == listing.id)
            )
        )
        .scalars()
        .all()
    )
    assert rows == []


@pytest.mark.asyncio
async def test_post_analysis_email_user_allowed(
    client: AsyncClient,
    session: AsyncSession,
    listing,
    fake_llm,
):
    """Email-пользователь тоже с id < 0, но с email → гость не он, разбор идёт."""
    emailer = User(
        telegram_id=-987654321,
        username="fixture_email",
        role="buyer",
        subscription_tier="free",
        is_active=True,
        email="user@mail.com",
        created_at=datetime.now(UTC),
    )
    session.add(emailer)
    await session.commit()

    resp = await client.post(
        f"/api/listings/{listing.id}/analysis",
        headers=_email_user_headers(emailer),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["verdict"] == "deal"


@pytest.mark.asyncio
async def test_bot_create_analysis_guest_forbidden(
    client: AsyncClient,
    internal_headers: dict[str, str],
    guest_user: User,
    listing,
):
    """Bot-канал тоже отказывает гостям (согласованность с web)."""
    r = await client.post(
        f"/api/bot/listings/{listing.id}/analysis",
        json={"user_id": guest_user.telegram_id},  # < 0
        headers=internal_headers,
    )
    assert r.status_code == 403
    assert "guest_forbidden" in r.json()["detail"]


@pytest.mark.asyncio
async def test_bot_analysis_requires_internal_token(client: AsyncClient, listing):
    r = await client.post(
        f"/api/bot/listings/{listing.id}/analysis", json={"user_id": 1}
    )
    assert r.status_code == 401


# ---------- web: GET кэша ----------


@pytest.mark.asyncio
async def test_get_analysis_returns_cached(
    client: AsyncClient, listing, session_with_analysis: AsyncSession
):
    r = await client.get(f"/api/listings/{listing.id}/analysis")
    assert r.status_code == 200
    body = r.json()
    assert body["verdict"] == "fair"


@pytest.mark.asyncio
async def test_get_analysis_none_when_missing(
    client: AsyncClient, listing
):
    r = await client.get("/api/listings/999999/analysis")
    assert r.status_code == 404  # листинга нет


# ---------- кэш LLM-клиента ----------


@pytest.mark.asyncio
async def test_llm_client_cached(
    client: AsyncClient,
    session: AsyncSession,
    pro_user: User,
    listing,
    monkeypatch,
):
    """build_llm_client вызывается один раз на (provider, model)."""
    from app.infrastructure.ai.llm_client import get_cached_llm_client

    calls = {"n": 0}

    def counting(*a, **kw):
        calls["n"] += 1
        return _FakeLLM()

    monkeypatch.setattr(
        "app.infrastructure.ai.llm_client.build_llm_client", counting
    )
    get_cached_llm_client.cache_clear()
    token = create_access_token(pro_user.telegram_id, get_settings())
    headers = {"Authorization": f"Bearer {token}"}
    try:
        for _ in range(3):
            await client.post(
                f"/api/listings/{listing.id}/analysis", headers=headers
            )
        assert calls["n"] == 1
    finally:
        get_cached_llm_client.cache_clear()
