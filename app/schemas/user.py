"""Схемы пользователя и авторизации (ТЗ п. 5.8: users)."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class TelegramAuthRequest(BaseModel):
    """Тело POST /api/auth/telegram — initData из Mini App."""

    init_data: str


class MagicLinkRequest(BaseModel):
    token: str


class UserUpsert(BaseModel):
    """Данные для создания/обновления пользователя (используется ботом)."""

    telegram_id: int
    username: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    language_code: str | None = None


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)  # валидация из ORM-объекта

    telegram_id: int
    username: str | None
    first_name: str | None
    last_name: str | None
    language_code: str | None
    role: Literal["buyer", "dealer", "salon", "admin"]
    subscription_tier: Literal["free", "pro", "business"]
    subscription_until: datetime | None
    created_at: datetime
    is_active: bool
    onboarded_at: datetime | None = None


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class AuthResponse(TokenPair):
    user: UserOut


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=10)
