"""AI-анализ стоимости (монетизация): POST делает разбор, GET отдаёт кэш.

WebApp/сайт — JWT (CurrentUserDep); бот — internal-роутер с X-Internal-Token.
Квота: free = 1 разбор одной машины в неделю; pro/business = безлимит.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.llm_client import build_llm_client
from app.core.config import get_settings
from app.core.deps import CurrentUserDep, SessionDep
from app.core.deps_internal import check_internal_token
from app.db.models import Listing, User
from app.services.price_analysis import QuotaExceeded, latest_analysis, run_analysis
from app.services.subscription import effective_tier

router = APIRouter(prefix="/api/listings", tags=["analysis"])
bot_router = APIRouter(
    prefix="/api/bot/listings",
    tags=["analysis"],
    dependencies=[Depends(check_internal_token)],
)


class BotAnalysisRequest(BaseModel):
    user_id: int  # telegram_id


class AnalysisOut(BaseModel):
    verdict: str
    price: float
    currency: str
    fair_min: float | None = None
    fair_max: float | None = None
    summary: str | None = None
    factors: list[str] = []
    comps: dict | None = None
    history: list[dict] = []
    source: str = "ai"
    created_at: str | None = None  # ISO — «разбор от …» в UI


def _is_web_guest(user: User) -> bool:
    """Гостевой аккаунт сайта (POST /auth/guest): синтетический telegram_id < 0
    и без email (email-регистрация тоже с id < 0, но с заполненным email)."""
    return getattr(user, "telegram_id", 0) < 0 and getattr(user, "email", None) is None


async def _get_listing(session: AsyncSession, listing_id: int) -> Listing:
    listing = await session.get(Listing, listing_id)
    if listing is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Listing not found")
    return listing


@router.post("/{listing_id}/analysis", response_model=AnalysisOut)
async def create_analysis(
    listing_id: int,
    user: CurrentUserDep,
    session: SessionDep,
) -> AnalysisOut:
    """Разбор цены через LLM (квота: 1/нед на машину для free).

    Гостям (POST /auth/guest) разбор закрыт — только авторизированным.
    """
    if _is_web_guest(user):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "guest_forbidden: AI-анализ цены доступен только после входа "
            "или регистрации",
        )
    listing = await _get_listing(session, listing_id)
    try:
        llm = build_llm_client(get_settings())
    except Exception as exc:  # нет ключа/конфига LLM
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, f"LLM unavailable: {exc}"
        ) from exc
    try:
        payload = await run_analysis(
            session, llm, listing, user.telegram_id, tier=effective_tier(user)
        )
    except QuotaExceeded as exc:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "quota_exceeded: 1 разбор этой машины в неделю на Free. Безлимит — в Pro",
        ) from exc
    finally:
        await llm.aclose()
    return AnalysisOut(**payload)


@router.get("/{listing_id}/analysis", response_model=AnalysisOut | None)
async def get_analysis(listing_id: int, session: SessionDep) -> AnalysisOut | None:
    """Кэш последнего разбора (общий на листинг) — без вызова LLM."""
    await _get_listing(session, listing_id)
    payload = await latest_analysis(session, listing_id)
    return AnalysisOut(**payload) if payload else None


@bot_router.post("/{listing_id}/analysis", response_model=AnalysisOut)
async def bot_create_analysis(
    listing_id: int,
    body: BotAnalysisRequest,
    session: SessionDep,
) -> AnalysisOut:
    """Тот же разбор по internal-токену: бот передаёт telegram_id юзера."""
    listing = await _get_listing(session, listing_id)
    user = await session.get(User, body.user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    try:
        llm = build_llm_client(get_settings())
    except Exception as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, f"LLM unavailable: {exc}"
        ) from exc
    try:
        payload = await run_analysis(
            session, llm, listing, body.user_id, tier=effective_tier(user)
        )
    except QuotaExceeded as exc:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "quota_exceeded: 1 разбор этой машины в неделю на Free. Безлимит — в Pro",
        ) from exc
    finally:
        await llm.aclose()
    return AnalysisOut(**payload)
