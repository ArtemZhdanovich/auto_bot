"""AI-анализ стоимости — тонкий контроллер (web JWT + bot internal).

POST запускает разбор (квота: free — 1 разбор машины в неделю), GET отдаёт
кэш без вызова LLM. Гостям анализ закрыт на обоих каналах — правило живёт
в сценарии `RunAnalysis`, здесь только маппинг в HTTP.
"""


from __future__ import annotations
from app.transport.http.deps.dishka import DishkaRoute, InternalDishkaRoute

import logging
from typing import Any, NoReturn

from fastapi import APIRouter, HTTPException, Path, status
from pydantic import BaseModel, Field

from app.application.dto.analysis import AnalysisCommand
from app.application.errors import AppError, Forbidden, NotFound
from app.application.dialog.use_cases import AiUnavailable
from app.transport.http.deps.auth import CurrentUserDep
from app.transport.http.deps.use_cases import GetLatestAnalysisDep, RunAnalysisDep

logger = logging.getLogger(__name__)

router = APIRouter(route_class=DishkaRoute, prefix="/api/listings", tags=["analysis"])
bot_router = APIRouter(route_class=InternalDishkaRoute,
    prefix="/api/bot/listings",
    tags=["analysis"],
)


class BotAnalysisRequest(BaseModel):
    """Запрос на анализ от бота (internal-токен)."""

    user_id: int  # telegram_id (PK users); для гостей < 0


class AnalysisOut(BaseModel):
    """Результат AI-анализа цены."""

    verdict: str
    price: float
    currency: str
    fair_min: float | None = None
    fair_max: float | None = None
    summary: str | None = None
    factors: list[str] = Field(default_factory=list)
    comps: dict[str, Any] | None = None
    history: list[dict[str, Any]] = Field(default_factory=list)
    source: str = "ai"
    created_at: str | None = None  # ISO — «разбор от …» в UI


def _raise_http(exc: AppError) -> NoReturn:
    """Прикладная ошибка → HTTP-ответ (единственное место маппинга)."""
    if isinstance(exc, NotFound):
        raise HTTPException(status.HTTP_404_NOT_FOUND, exc.public_message)
    if isinstance(exc, Forbidden):
        raise HTTPException(status.HTTP_403_FORBIDDEN, exc.public_message)
    if isinstance(exc, AiUnavailable):
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "AI temporarily unavailable"
        )
    logger.error("analysis_unexpected_app_error code=%s", exc.code)
    raise HTTPException(status.HTTP_400_BAD_REQUEST, exc.public_message)


def _to_analysis_out(payload: dict[str, Any]) -> AnalysisOut:
    """Валидирует payload в `AnalysisOut`; ошибка схемы → 502.

    Args:
        payload: Результат `PriceAnalysisResult.to_payload` / кэш.

    Returns:
        `AnalysisOut`.

    Raises:
        HTTPException: 502 если payload не соответствует схеме.
    """
    try:
        return AnalysisOut.model_validate(payload)
    except Exception:  # noqa: BLE001 — bad upstream, а не 500
        logger.exception("analysis_payload_invalid payload_keys=%s", list(payload))
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, "Analysis payload is invalid"
        )


@router.post("/{listing_id}/analysis", response_model=AnalysisOut)
async def create_analysis(
    user: CurrentUserDep,
    use_case: RunAnalysisDep,
    listing_id: int = Path(ge=1),
) -> AnalysisOut:
    """Разбор цены через LLM (квота: 1/нед на машину для free).

    Гостям (POST /auth/guest) разбор закрыт — только авторизированным.
    """
    try:
        result = await use_case.execute(
            AnalysisCommand(
                user_id=user.telegram_id,
                listing_id=listing_id,
                channel="web",
            )
        )
    except AppError as exc:
        _raise_http(exc)
    return _to_analysis_out(result.to_payload())


@router.get("/{listing_id}/analysis", response_model=AnalysisOut | None)
async def get_analysis(
    use_case: GetLatestAnalysisDep,
    listing_id: int = Path(ge=1),
) -> AnalysisOut | None:
    """Кэш последнего разбора (общий на листинг) — без вызова LLM."""
    try:
        payload = await use_case.execute(listing_id)
    except AppError as exc:
        _raise_http(exc)
    return _to_analysis_out(payload) if payload else None


@bot_router.post("/{listing_id}/analysis", response_model=AnalysisOut)
async def bot_create_analysis(
    body: BotAnalysisRequest,
    use_case: RunAnalysisDep,
    listing_id: int = Path(ge=1),
) -> AnalysisOut:
    """Тот же разбор по internal-токену: бот передаёт telegram_id юзера.

    Guest-правило применяется и здесь (согласованность с web-каналом).
    """
    try:
        result = await use_case.execute(
            AnalysisCommand(
                user_id=body.user_id,
                listing_id=listing_id,
                channel="bot",
            )
        )
    except AppError as exc:
        _raise_http(exc)
    return _to_analysis_out(result.to_payload())
