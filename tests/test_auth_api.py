"""Тесты входа через Telegram (веб-режим): /auth/telegram/link + confirm."""

import json
from datetime import datetime, timezone

import pytest

from app.config import get_settings
from app.transport.http.deps.internal import internal_token
from app.infrastructure.security.tokens import create_access_token, create_refresh_token
from app.infrastructure.security.login_codes import Pbkdf2PasswordHasher
from app.infrastructure.db.models import User
from app.main_api import app

from conftest import FakeRedis, FakeSession, install_test_container

INTERNAL = {"X-Internal-Token": internal_token()}


@pytest.fixture
def tg_client(fake_redis: FakeRedis):
    from httpx import ASGITransport, AsyncClient

    session = FakeSession()
    _prev = app.state.dishka_container
    install_test_container(app, session=session, redis=fake_redis)
    yield AsyncClient(transport=ASGITransport(app=app), base_url="http://test"), session
    app.state.dishka_container = _prev


@pytest.mark.asyncio
async def test_link_requires_bot_username(tg_client, monkeypatch):
    http, _ = tg_client
    monkeypatch.setattr(get_settings(), "bot_username", "")
    resp = await http.post("/api/auth/telegram/link")
    assert resp.status_code == 503


@pytest.mark.asyncio
async def test_link_built_from_bot_username(tg_client, fake_redis, monkeypatch):
    """Ссылка на бота строится из BOT_USERNAME (env-значение без '@')."""
    http, _ = tg_client
    monkeypatch.setattr(get_settings(), "bot_username", "my_auto_bot")
    resp = await http.post("/api/auth/telegram/link")
    assert resp.status_code == 200
    body = resp.json()
    assert body["url"].startswith("https://t.me/my_auto_bot?start=login_")
    assert body["expires_in"] == 900
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
async def test_login_code_single_use_replay_expired(tg_client, monkeypatch):
    """Security: повторный обмен выданного кода не отдаёт второй JWT."""
    http, session = tg_client
    monkeypatch.setattr(get_settings(), "bot_username", "mybot")
    session.add(
        User(
            telegram_id=888,
            role="buyer",
            subscription_tier="free",
            is_active=True,
            created_at=datetime.now(timezone.utc),
        )
    )
    link = (await http.post("/api/auth/telegram/link")).json()
    confirm = await http.post(
        "/api/auth/telegram/link/confirm",
        headers=INTERNAL,
        json={"code": link["code"], "telegram_id": 888},
    )
    assert confirm.status_code == 200

    first = await http.get(f"/api/auth/telegram/link/{link['code']}")
    assert first.status_code == 200
    assert first.json()["access_token"]

    # Код одноразовый: повторный обмен (replay) токенов не даёт.
    replay = await http.get(f"/api/auth/telegram/link/{link['code']}")
    assert replay.status_code == 200
    assert replay.json() == {"status": "expired"}


@pytest.mark.asyncio
async def test_login_code_bound_to_browser(tg_client, monkeypatch):
    """Security: чужой браузер (без state-cookie) код не обменяет."""
    from httpx import ASGITransport, AsyncClient

    http, _ = tg_client
    monkeypatch.setattr(get_settings(), "bot_username", "mybot")
    link = (await http.post("/api/auth/telegram/link")).json()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as other:
        resp = await other.get(f"/api/auth/telegram/link/{link['code']}")

    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_login_context_and_cancel(tg_client, monkeypatch):
    """Internal context/cancel: бот видит инициатора и может отменить код."""
    http, _ = tg_client
    monkeypatch.setattr(get_settings(), "bot_username", "mybot")
    link = (await http.post("/api/auth/telegram/link")).json()

    ctx = await http.get(
        f"/api/auth/telegram/link/{link['code']}/context", headers=INTERNAL
    )
    assert ctx.status_code == 200
    context = ctx.json()
    assert context["code"] == link["code"]
    assert context["status"] == "pending"
    assert "initiator_ip" in context and "created_at" in context

    # «❌ Отмена» в боте: код умирает, обменять его уже нельзя.
    cancel = await http.delete(
        f"/api/auth/telegram/link/{link['code']}", headers=INTERNAL
    )
    assert cancel.status_code == 200
    assert cancel.json() == {"status": "cancelled"}
    status_resp = await http.get(f"/api/auth/telegram/link/{link['code']}")
    assert status_resp.json() == {"status": "expired"}

    # Повторная отмена — 404.
    again = await http.delete(
        f"/api/auth/telegram/link/{link['code']}", headers=INTERNAL
    )
    assert again.status_code == 404


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


# ---------------------------------------------------------------------------
# Веб-регистрация / вход (409/422/401/403)
# ---------------------------------------------------------------------------


def _web_user(**overrides) -> User:
    """Аккаунт сайта: email + пароль, полный набор полей для `UserOut`."""
    fields = {
        "telegram_id": 222222,
        "username": "webuser",
        "first_name": "Web",
        "email": "user@example.com",
        "role": "buyer",
        "subscription_tier": "free",
        "is_active": True,
        "created_at": datetime.now(timezone.utc),
    }
    fields.update(overrides)
    return User(**fields)


@pytest.mark.asyncio
async def test_register_existing_email_409(tg_client):
    """Занятый email — 409, без создания второго аккаунта."""
    http, session = tg_client
    session.set_result([_web_user()])
    resp = await http.post(
        "/api/auth/register",
        json={"email": "user@example.com", "password": "password123"},
    )
    assert resp.status_code == 409
    assert session.added == []


@pytest.mark.asyncio
async def test_register_invalid_email_422(tg_client):
    """Невалидный email — 422 (проверка в сценарии, не только в схеме)."""
    http, session = tg_client
    resp = await http.post(
        "/api/auth/register",
        json={"email": "not-an-email", "password": "password123"},
    )
    assert resp.status_code == 422
    assert session.added == []


@pytest.mark.asyncio
async def test_login_returns_tokens(tg_client):
    """Верный пароль — JWT-пара и профиль."""
    http, session = tg_client
    password_hash = await Pbkdf2PasswordHasher().hash("secret123")
    session.set_result([_web_user(password_hash=password_hash)])
    resp = await http.post(
        "/api/auth/login",
        json={"email": "user@example.com", "password": "secret123"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["access_token"] and body["refresh_token"]
    assert body["user"]["telegram_id"] == 222222


@pytest.mark.asyncio
async def test_login_wrong_password_401(tg_client):
    """Неверный пароль — 401 без утечки деталей."""
    http, session = tg_client
    password_hash = await Pbkdf2PasswordHasher().hash("secret123")
    session.set_result([_web_user(password_hash=password_hash)])
    resp = await http.post(
        "/api/auth/login",
        json={"email": "user@example.com", "password": "wrong-pass"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_login_inactive_user_403(tg_client):
    """Забаненный пользователь: верный пароль всё равно даёт 403."""
    http, session = tg_client
    password_hash = await Pbkdf2PasswordHasher().hash("secret123")
    session.set_result([_web_user(password_hash=password_hash, is_active=False)])
    resp = await http.post(
        "/api/auth/login",
        json={"email": "user@example.com", "password": "secret123"},
    )
    assert resp.status_code == 403
