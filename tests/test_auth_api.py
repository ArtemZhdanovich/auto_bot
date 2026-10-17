"""Тесты входа через Telegram (веб-режим): /auth/telegram/link + confirm."""

import json
from datetime import datetime, timezone

import pytest

from app.core.config import get_settings
from app.core.deps_internal import get_redis, internal_token
from app.core.security import create_access_token, create_refresh_token
from app.db.models import User
from app.db.session import get_session
from app.main import app

from conftest import FakeRedis, FakeSession

INTERNAL = {"X-Internal-Token": internal_token()}


@pytest.fixture
def tg_client(fake_redis: FakeRedis):
    from httpx import ASGITransport, AsyncClient

    session = FakeSession()
    app.dependency_overrides[get_redis] = lambda: fake_redis
    app.dependency_overrides[get_session] = lambda: session
    yield AsyncClient(transport=ASGITransport(app=app), base_url="http://test"), session
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_link_requires_bot_username(tg_client, monkeypatch):
    http, _ = tg_client
    monkeypatch.setattr(get_settings(), "bot_username", "")
    resp = await http.post("/api/auth/telegram/link")
    assert resp.status_code == 503


@pytest.mark.asyncio
async def test_link_built_from_bot_username(tg_client, fake_redis, monkeypatch):
    """Ссылка на бота строится из env BOT_USERNAME (без @)."""
    http, _ = tg_client
    monkeypatch.setattr(get_settings(), "bot_username", "@my_auto_bot")
    resp = await http.post("/api/auth/telegram/link")
    assert resp.status_code == 200
    body = resp.json()
    assert body["url"].startswith("https://t.me/my_auto_bot?start=login_")
    assert body["expires_in"] == 600
    # код сохранён в redis как pending
    code = body["code"]
    assert json.loads(fake_redis.data[f"tglogin:{code}"])["status"] == "pending"


@pytest.mark.asyncio
async def test_full_login_flow(tg_client, fake_redis, monkeypatch):
    """link → confirm (internal, как /start login_ в боте) → GET отдаёт JWT."""
    http, session = tg_client
    monkeypatch.setattr(get_settings(), "bot_username", "mybot")
    # пользователь с этим telegram_id уже есть (role/tier — Python-side дефолты
    # SQLAlchemy проставляются только при flush, поэтому задаём явно)
    session.add(
        User(
            telegram_id=777,
            username="old",
            role="buyer",
            subscription_tier="free",
            is_active=True,
            created_at=datetime.now(timezone.utc),
        )
    )
    link = (await http.post("/api/auth/telegram/link")).json()

    # до подтверждения — pending
    status = (await http.get(f"/api/auth/telegram/link/{link['code']}")).json()
    assert status == {"status": "pending"}

    # confirm без internal-токена не принимается
    denied = await http.post(
        "/api/auth/telegram/link/confirm",
        json={"code": link["code"], "telegram_id": 777},
    )
    assert denied.status_code == 401

    ok = await http.post(
        "/api/auth/telegram/link/confirm",
        headers=INTERNAL,
        json={
            "code": link["code"],
            "telegram_id": 777,
            "username": "tguser",
            "first_name": "Иван",
        },
    )
    assert ok.status_code == 200
    assert ok.json() == {"status": "ok"}

    # после подтверждения — JWT-пара
    done = await http.get(f"/api/auth/telegram/link/{link['code']}")
    assert done.status_code == 200
    body = done.json()
    assert body["access_token"]
    assert body["refresh_token"]
    assert body["user"]["first_name"] == "Иван"
    assert body["user"]["username"] == "tguser"

    # код одноразовый: повторный GET — expired
    again = (await http.get(f"/api/auth/telegram/link/{link['code']}")).json()
    assert again == {"status": "expired"}
    assert f"tglogin:{link['code']}" not in fake_redis.data


@pytest.mark.asyncio
async def test_confirm_unknown_code_404(tg_client):
    http, _ = tg_client
    resp = await http.post(
        "/api/auth/telegram/link/confirm",
        headers=INTERNAL,
        json={"code": "no-such-code-here", "telegram_id": 1},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_status_unknown_code_expired(tg_client):
    http, _ = tg_client
    resp = await http.get("/api/auth/telegram/link/no-such-code-here")
    assert resp.status_code == 200
    assert resp.json() == {"status": "expired"}


def _user() -> User:
    # is_active задаём явно: Python-side default SQLAlchemy применяется при
    # flush, а без БД объект остаётся с is_active=None.
    return User(
        telegram_id=111111, username="webuser", subscription_tier="free", is_active=True
    )


@pytest.mark.asyncio
async def test_refresh_returns_new_token_pair(tg_client):
    http, session = tg_client
    session.add(_user())
    settings = get_settings()
    token = create_refresh_token(111111, settings)
    resp = await http.post("/api/auth/refresh", json={"refresh_token": token})
    assert resp.status_code == 200
    body = resp.json()
    assert body["access_token"]
    assert body["refresh_token"]
    assert body["token_type"] == "bearer"


@pytest.mark.asyncio
async def test_access_token_rejected_as_refresh(tg_client):
    """Access-токен в поле refresh_token не принимается (type mismatch)."""
    http, session = tg_client
    session.add(_user())
    settings = get_settings()
    token = create_access_token(111111, settings)
    resp = await http.post("/api/auth/refresh", json={"refresh_token": token})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_garbage_token_401(tg_client):
    http, _ = tg_client
    resp = await http.post(
        "/api/auth/refresh", json={"refresh_token": "not-a-jwt-value"}
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_unknown_user_401(tg_client):
    """Валидный refresh-токен, но пользователя нет/деактивирован → 401."""
    http, session = tg_client
    settings = get_settings()
    token = create_refresh_token(999999, settings)
    resp = await http.post("/api/auth/refresh", json={"refresh_token": token})
    assert resp.status_code == 401
