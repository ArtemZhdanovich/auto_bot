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
    )
    assert s.app_env == "prod"
