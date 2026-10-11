"""Схемы партнёрского контура (P2-11): аккаунты, API-ключи, выдача лидов."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

PartnerStatus = Literal["claimed", "accepted", "returned", "junk", "deal"]
PartnerPlan = Literal["starter", "pro", "salon"]


# --- Admin (X-Internal-Token) -----------------------------------------------


class PartnerCreateIn(BaseModel):
    """POST /api/admin/partners — создать партнёра и выдать API-ключ."""

    name: str = Field(min_length=1, max_length=200)
    contact: str | None = Field(default=None, max_length=200)
    plan: PartnerPlan = "starter"
    daily_quota: int = Field(default=10, ge=1, le=1000)


class PartnerUpdateIn(BaseModel):
    """PATCH /api/admin/partners/{id} — план/квота/статус."""

    plan: PartnerPlan | None = None
    daily_quota: int | None = Field(default=None, ge=1, le=1000)
    status: Literal["active", "suspended"] | None = None


class PartnerKeyOut(BaseModel):
    """Сырой ключ показывается ОДИН раз (хранится только SHA-256 хэш)."""

    model_config = ConfigDict(from_attributes=True)

    partner_id: int
    key: str
    key_prefix: str


class PartnerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    contact: str | None
    plan: str
    daily_quota: int
    status: str
    created_at: datetime


class PartnerWithStatsOut(PartnerOut):
    claimed_today: int = 0
    key_prefix: str | None = None


# --- Partner API (X-API-Key) ------------------------------------------------


class PartnerMeOut(BaseModel):
    """GET /api/partner/me — состояние аккаунта для ЛК."""

    id: int
    name: str
    plan: str
    daily_quota: int
    claimed_today: int
    quota_remaining: int
    key_prefix: str | None


class PartnerLeadOut(BaseModel):
    """Лид в партнёрском пуле. ПД отдаём только после claim (эксклюзивно)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    source: str | None
    status: str
    comment: str | None
    qualification: dict[str, Any] | None
    created_at: datetime
    partner_id: int | None = None
    partner_status: str | None = None
    claimed_at: datetime | None = None
    user_id: int | None = None  # заполняется после claim


class ClaimOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    lead_id: int
    partner_id: int
    partner_status: str
    user_id: int | None
    qualification: dict[str, Any] | None
    claimed_at: datetime | None


class FeedbackIn(BaseModel):
    """POST /api/partner/leads/{id}/feedback — статус качества лида."""

    status: PartnerStatus