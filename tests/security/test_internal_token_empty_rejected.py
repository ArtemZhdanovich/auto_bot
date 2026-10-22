# tests/core/test_config.py
import pytest
from fastapi import HTTPException
from pydantic import SecretStr, ValidationError

from app.config import Settings, get_settings
from app.transport.http.deps.internal import check_internal_token, internal_token

SECRET = "secret"


def _clear(monkeypatch, **env):
    for k in [
        "APP_ENV", "APP_SECRET_KEY", "INTERNAL_TOKEN",
        "JWT_ALGORITHM", "LOG_LEVEL", "LLM_PROVIDER",
    ]:
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)


def test_prod_requires_internal_token(monkeypatch):
    """`_env_file=None`: проверяем ровно заданные переменные, а не репо-`.env`."""
    _clear(monkeypatch, APP_ENV="prod", APP_SECRET_KEY="x" * 64)
    with pytest.raises(ValidationError, match="INTERNAL_TOKEN"):
        Settings(_env_file=None)


def test_prod_internal_token_empty_secret_rejected(monkeypatch):
    """SecretStr('') truthy — регрессия на баг `if not self.internal_token`."""
    _clear(
        monkeypatch,
        APP_ENV="prod",
        APP_SECRET_KEY="x" * 64,
        INTERNAL_TOKEN="",
    )
    with pytest.raises(ValidationError, match="INTERNAL_TOKEN"):
        Settings(_env_file=None)


def test_prod_app_env_normalized(monkeypatch):
    _clear(
        monkeypatch,
        APP_ENV="PROD",
        APP_SECRET_KEY="x" * 64,
        INTERNAL_TOKEN="y" * 48,
        # TECHDEBT 1.6: дев-дефолты URL в prod запрещены — задаём «настоящие»
        DATABASE_URL="postgresql+asyncpg://u:p@db.internal:5432/autoselect",
        RABBITMQ_URL="amqp://svc:pw@mq.internal:5672/",
        REDIS_URL="redis://redis.internal:6379/0",
    )
    assert Settings().app_env == "prod"


def test_prod_app_env_production_alias(monkeypatch):
    _clear(
        monkeypatch,
        APP_ENV=" production ",
        APP_SECRET_KEY="x" * 64,
        INTERNAL_TOKEN="y" * 48,
        DATABASE_URL="postgresql+asyncpg://u:p@db.internal:5432/autoselect",
        RABBITMQ_URL="amqp://svc:pw@mq.internal:5672/",
        REDIS_URL="redis://redis.internal:6379/0",
    )
    assert Settings().app_env == "prod"


def test_prod_dev_default_rejected(monkeypatch):
    _clear(monkeypatch, APP_ENV="prod", INTERNAL_TOKEN="y" * 48)
    with pytest.raises(ValidationError, match="APP_SECRET_KEY"):
        Settings(_env_file=None)


def test_jwt_algorithm_none_rejected(monkeypatch):
    _clear(monkeypatch, JWT_ALGORITHM="none")
    with pytest.raises(ValidationError):
        Settings()


def test_secrets_not_in_repr(monkeypatch):
    _clear(monkeypatch, INTERNAL_TOKEN="super-secret-value")
    s = Settings()
    assert "super-secret-value" not in repr(s)
    assert "super-secret-value" not in str(s.model_dump())


def test_proxy_user_pass_pair(monkeypatch):
    _clear(monkeypatch, PROXY_DATAIMPULSE_USER="u")
    with pytest.raises(ValidationError, match="задаются парой"):
        Settings()


def test_s3_enabled_requires_creds(monkeypatch):
    _clear(monkeypatch, RAW_PAYLOAD_ENABLED="true", S3_ENDPOINT_URL="http://s3:9000")
    with pytest.raises(ValidationError, match="S3_ACCESS_KEY"):
        Settings()


def test_refresh_must_exceed_access(monkeypatch):
    _clear(monkeypatch, ACCESS_TOKEN_TTL_SEC="1000", REFRESH_TOKEN_TTL_SEC="1000")
    with pytest.raises(ValidationError, match="REFRESH_TOKEN_TTL_SEC"):
        Settings()


def test_internal_token_empty_rejected(monkeypatch):
    """Оба секрета пустые → 503, а не 200-байпас."""
    settings = get_settings()
    monkeypatch.setattr(settings, "internal_token", SecretStr(""))
    monkeypatch.setattr(settings, "app_secret_key", SecretStr(""))
    with pytest.raises(HTTPException) as exc:
        internal_token()
    assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_internal_token_header_empty_rejected(client, monkeypatch):
    """Секрет настроен, заголовок пустой → 401."""
    monkeypatch.setattr(get_settings(), "internal_token", SecretStr(SECRET))
    r = await client.get("/metrics", headers={"X-Internal-Token": ""})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_internal_token_header_missing_rejected(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "internal_token", SecretStr(SECRET))
    r = await client.get("/metrics")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_internal_token_header_wrong_rejected(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "internal_token", SecretStr(SECRET))
    r = await client.get("/metrics", headers={"X-Internal-Token": "wrong"})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_internal_token_header_valid_accepted(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "internal_token", SecretStr(SECRET))
    r = await client.get("/metrics", headers={"X-Internal-Token": SECRET})
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_internal_token_non_ascii_rejected_not_500(monkeypatch):
    """Non-ASCII в токене не должен ронять compare_digest в 500.

    httpx/ASGI не пропускают non-ASCII в заголовке (UnicodeEncodeError ещё
    на клиенте), поэтому проверяем зависимость напрямую.
    """
    monkeypatch.setattr(get_settings(), "internal_token", SecretStr(SECRET))
    with pytest.raises(HTTPException) as exc:
        check_internal_token(b"\xff\xfe".decode("latin-1"))
    assert exc.value.status_code == 401


def test_internal_token_fallback_to_app_secret(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "internal_token", SecretStr(""))
    monkeypatch.setattr(settings, "app_secret_key", SecretStr("jwt-secret"))
    assert internal_token() == "jwt-secret"


@pytest.mark.asyncio
async def test_redis_requires_url(monkeypatch):
    """RedisProvider fail-fast: пустой REDIS_URL → RuntimeError при сборке."""
    from app.di.providers.redis import RedisProvider

    monkeypatch.setattr(get_settings(), "redis_url", "")
    gen = RedisProvider().redis(get_settings())
    with pytest.raises(RuntimeError):
        await anext(gen)
