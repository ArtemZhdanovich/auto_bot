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
    balance: float = Field(default=0.0, ge=0, le=1_000_000)
    cpl_rate: float | None = Field(default=None, ge=0, le=10_000)


class PartnerUpdateIn(BaseModel):
    """PATCH /api/admin/partners/{id} — план/квота/статус/тариф.

    cpl_rate None = не менять; reset_cpl_rate=True → вернуть тариф плана.
    """

    plan: PartnerPlan | None = None
    daily_quota: int | None = Field(default=None, ge=1, le=1000)
    status: Literal["active", "suspended"] | None = None
    cpl_rate: float | None = Field(default=None, ge=0, le=10_000)
    reset_cpl_rate: bool = False


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
    balance: float = 0.0
    cpl_rate: float | None = None
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
    balance: float  # CPL-биллинг (P2-14)
    cpl_cost: float  # цена следующего лида
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


class BatchClaimIn(BaseModel):
    """POST /api/partner/leads/batch — взять N лидов одной кнопкой."""

    limit: int = Field(default=3, ge=1, le=10)


class BatchClaimOut(BaseModel):
    """Результат batch-claim: захваченные лиды (исчерпание квоты → 429)."""

    claimed: list[ClaimOut]


# --- Биллинг (тарифы CPL) -----------------------------------------------------


class PlanInfo(BaseModel):
    """Тариф из каталога (значения по умолчанию квоты; фактическая — у партнёра)."""

    key: PartnerPlan
    title: str
    daily_quota: int
    price_usd: int


class BillingOut(BaseModel):
    """GET /api/partner/billing — план, баланс, расход квоты, каталог тарифов."""

    plan: str
    daily_quota: int
    claimed_today: int
    quota_remaining: int
    balance: float  # CPL-биллинг (P2-14)
    cpl_cost: float  # цена следующего лида
    plans: list[PlanInfo]


class BillingTopupIn(BaseModel):
    """POST /api/admin/partners/{id}/billing — пополнение/корректировка баланса.

    amount > 0 — пополнение (снимает suspended при исчерпании),
    amount < 0 — ручная корректировка (kind=adjustment).
    """

    amount: float = Field(ge=-100_000, le=100_000)
    comment: str | None = Field(default=None, max_length=500)


"""Схемы партнёрского контура (P2-11, P2-14).

`Literal`-поля защищают от мусора в БД (`plan`, `status`, `feedback`).
Границы `amount`/`daily_quota`/`limit` — от абуза.
"""

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

PartnerPlan = Literal["starter", "pro", "salon"]
PartnerStatus = Literal["active", "suspended"]
PartnerFeedback = Literal["accepted", "returned", "junk", "deal"]


# --- Admin ---------------------------------------------------------


class PartnerCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    contact: str | None = Field(default=None, max_length=128)
    plan: PartnerPlan = "starter"
    daily_quota: int = Field(default=10, ge=0, le=10_000)
    balance: Decimal = Field(default=Decimal("0"), ge=0, le=Decimal("100000000"))
    cpl_rate: Decimal | None = Field(default=None, gt=0, le=Decimal("10000"))


class PartnerUpdateIn(BaseModel):
    plan: PartnerPlan | None = None
    daily_quota: int | None = Field(default=None, ge=0, le=10_000)
    status: PartnerStatus | None = None
    cpl_rate: Decimal | None = Field(default=None, gt=0, le=Decimal("10000"))
    reset_cpl_rate: bool = False


class PartnerKeyOut(BaseModel):
    partner_id: int
    key: str  # raw key — показывается ОДИН раз
    key_prefix: str


class PartnerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    contact: str | None
    plan: PartnerPlan
    daily_quota: int
    status: PartnerStatus
    balance: Decimal
    cpl_rate: Decimal | None
    created_at: datetime


class PartnerWithStatsOut(PartnerOut):
    claimed_today: int
    key_prefix: str | None = None  # активный ключ, если есть


class BillingTopupIn(BaseModel):
    amount: Decimal = Field(
        ..., ne=0, ge=Decimal("-1000000"), le=Decimal("1000000")
    )
    comment: str | None = Field(default=None, max_length=500)


# --- Partner API ---------------------------------------------------


class PartnerMeOut(BaseModel):
    id: int
    name: str
    plan: PartnerPlan
    daily_quota: int
    claimed_today: int
    quota_remaining: int
    balance: Decimal
    cpl_cost: Decimal
    key_prefix: str | None = None


class PartnerLeadPublicOut(BaseModel):
    """Пул лидов: без PII (`user_id` не отдаётся до claim)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    source: str | None
    status: str
    comment: str | None
    qualification: dict | None
    created_at: datetime


class PartnerLeadOwnedOut(PartnerLeadPublicOut):
    """«Мои лиды»: с PII (после claim)."""

    user_id: int
    partner_status: str | None = None


# Backward-compat: старые импорты `PartnerLeadOut` (если есть) —
# в новом коде использовать Public/Owned явно.
PartnerLeadOut = PartnerLeadPublicOut


class BatchClaimIn(BaseModel):
    limit: int = Field(default=3, ge=1, le=10)


class ClaimOut(BaseModel):
    lead_id: int
    partner_id: int
    partner_status: str | None
    user_id: int
    qualification: dict | None
    claimed_at: datetime | None


class BatchClaimOut(BaseModel):
    claimed: list[ClaimOut]


class FeedbackIn(BaseModel):
    status: PartnerFeedback


class PlanInfo(BaseModel):
    key: PartnerPlan
    title: str
    daily_quota: int
    cpl_cost: Decimal
    description: str | None = None


class BillingOut(BaseModel):
    plan: PartnerPlan
    daily_quota: int
    claimed_today: int
    quota_remaining: int
    balance: Decimal
    cpl_cost: Decimal
    plans: list[PlanInfo]