"""INC-004: fail-fast — в prod запрещены дев-секрет и короткий APP_SECRET_KEY."""

import pytest
from pydantic import ValidationError

from app.config import Settings


def test_dev_allows_default_secret():
    s = Settings(_env_file=None, app_env="dev")  # noqa: S106 — дев-дефолт в dev разрешён
    assert s.app_secret_key.get_secret_value().startswith("dev-secret")


def test_prod_rejects_default_secret():
    with pytest.raises(ValidationError, match="dev-дефолт"):
        Settings(_env_file=None, app_env="prod")


def test_prod_rejects_short_secret():
    with pytest.raises(ValidationError, match="короче 32"):
        Settings(_env_file=None, app_env="prod", app_secret_key="short")


def test_prod_accepts_strong_secret():
    s = Settings(
        _env_file=None,
        app_env="prod",
        app_secret_key="a" * 64,
        internal_token="i" * 48,
        llm_provider="mock",  # в prod без ключа провайдера валидация не пройдёт
        database_url="postgresql+asyncpg://u:p@db.internal:5432/autoselect",
        rabbitmq_url="amqp://svc:pw@mq.internal:5672/",
        redis_url="redis://redis.internal:6379/0",
        centrifugo_secret="cs" * 16,  # чаты: дев-секрет centrifugo в prod запрещён
        centrifugo_http_api_key="ck" * 16,
    )
    assert s.app_env == "prod"


# --- Чаты (05 §4): дев-секреты centrifugo в prod запрещены ----------------


def test_prod_rejects_default_centrifugo_secret():
    """change-me позволяет фальшивый ws-JWT → чужой канал user:{id}."""
    with pytest.raises(ValidationError, match="CENTRIFUGO_SECRET"):
        Settings(
            _env_file=None,
            app_env="prod",
            app_secret_key="a" * 64,
            internal_token="i" * 48,
            llm_provider="mock",
            database_url="postgresql+asyncpg://u:p@db.internal:5432/autoselect",
            rabbitmq_url="amqp://svc:pw@mq.internal:5672/",
            redis_url="redis://redis.internal:6379/0",
            centrifugo_http_api_key="ck" * 16,
        )


def test_prod_rejects_default_centrifugo_api_key():
    with pytest.raises(ValidationError, match="CENTRIFUGO_HTTP_API_KEY"):
        Settings(
            _env_file=None,
            app_env="prod",
            app_secret_key="a" * 64,
            internal_token="i" * 48,
            llm_provider="mock",
            database_url="postgresql+asyncpg://u:p@db.internal:5432/autoselect",
            rabbitmq_url="amqp://svc:pw@mq.internal:5672/",
            redis_url="redis://redis.internal:6379/0",
            centrifugo_secret="cs" * 16,
        )


# --- TECHDEBT 1.6: дев-дефолты URL в prod запрещены ----------------------


def test_prod_rejects_default_db_url():
    with pytest.raises(ValidationError, match="DATABASE_URL не задан"):
        Settings(
            _env_file=None,
            app_env="prod",
            app_secret_key="a" * 64,
            internal_token="i" * 48,
            llm_provider="mock",
            database_url="postgresql+asyncpg://app:app@localhost:5432/autoselect",
            rabbitmq_url="amqp://svc:pw@mq.internal:5672/",
            redis_url="redis://redis.internal:6379/0",
            centrifugo_secret="cs" * 16,
            centrifugo_http_api_key="ck" * 16,
        )


def test_prod_rejects_default_rabbitmq_url():
    with pytest.raises(ValidationError, match="RABBITMQ_URL"):
        Settings(
            _env_file=None,
            app_env="prod",
            app_secret_key="a" * 64,
            internal_token="i" * 48,
            llm_provider="mock",
            database_url="postgresql+asyncpg://u:p@db.internal:5432/autoselect",
            rabbitmq_url="amqp://guest:guest@localhost:5672/",
            redis_url="redis://redis.internal:6379/0",
            centrifugo_secret="cs" * 16,
            centrifugo_http_api_key="ck" * 16,
        )


def test_prod_rejects_rabbitmq_guest_creds():
    """Даже не-дефолтный хост, но guest:guest — запрещено (known creds)."""
    with pytest.raises(ValidationError, match="guest:guest"):
        Settings(
            _env_file=None,
            app_env="prod",
            app_secret_key="a" * 64,
            internal_token="i" * 48,
            llm_provider="mock",
            database_url="postgresql+asyncpg://u:p@db.internal:5432/autoselect",
            rabbitmq_url="amqp://guest:guest@mq.internal:5672/",
            redis_url="redis://redis.internal:6379/0",
            centrifugo_secret="cs" * 16,
            centrifugo_http_api_key="ck" * 16,
        )


def test_prod_rejects_default_redis_url():
    with pytest.raises(ValidationError, match="REDIS_URL не задан"):
        Settings(
            _env_file=None,
            app_env="prod",
            app_secret_key="a" * 64,
            internal_token="i" * 48,
            llm_provider="mock",
            database_url="postgresql+asyncpg://u:p@db.internal:5432/autoselect",
            rabbitmq_url="amqp://svc:pw@mq.internal:5672/",
            redis_url="redis://localhost:6379/0",
            centrifugo_secret="cs" * 16,
            centrifugo_http_api_key="ck" * 16,
        )
