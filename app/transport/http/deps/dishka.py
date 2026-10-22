"""DishkaRoute с разрешением return-аннотации (future annotations).

`inject` из dishka компилирует новую функцию-обёртку: её `__globals__` —
не модуль роутера, поэтому FastAPI не может вычислить строковую
return-аннотацию (`from __future__ import annotations`) и строит
response-модель от неразрешённого `ForwardRef`. Здесь сигнатура обёртки
пересобирается с уже вычисленным типом возврата (глобалы оригинала).
"""

from __future__ import annotations

from inspect import Parameter, Signature, iscoroutinefunction, signature
from typing import Annotated, Any, get_type_hints

from dishka.integrations.fastapi import inject
from fastapi import Header
from fastapi.routing import APIRoute

from app.transport.http.deps.internal import check_internal_token


def _inject_and_resolve(endpoint):
    """Обернуть эндпоинт в dishka-inject и починить return-аннотацию.

    Args:
        endpoint: Функция-обработчик роута.

    Returns:
        Обёртка с `__signature__.return_annotation` — конкретным типом.
    """
    injected = inject(endpoint)
    sig = signature(injected)
    orig_ret = sig.return_annotation
    if isinstance(orig_ret, str):
        # строковая аннотация (future annotations): у обёртки dishka чужие
        # globals — вычисляем тип в глобалах оригинального модуля. `None`
        # не трогаем: он вычисляется и так (status 204 без тела).
        ret = get_type_hints(endpoint).get("return", orig_ret)
        if ret is not None and ret is not type(None):
            injected.__signature__ = sig.replace(return_annotation=ret)
    return injected


class DishkaRoute(APIRoute):
    """APIRoute c автоинжектом `FromDishka` (см. module docstring)."""

    def __init__(
        self,
        path: str,
        endpoint,  # noqa: ANN001 — сигнатура FastAPI
        **kwargs,
    ) -> None:
        super().__init__(path, _inject_and_resolve(endpoint), **kwargs)


# --- Внутренние эндпоинты: X-Internal-Token без FastAPI Depends ------------

_INTERNAL_TOKEN_PARAM = Parameter(
    name="x_internal_token",
    annotation=Annotated[str | None, Header(alias="X-Internal-Token")],
    kind=Parameter.KEYWORD_ONLY,
    default=None,
)


def _guarded_internal(endpoint):
    """Проверить X-Internal-Token до выполнения эндпоинта (без Depends).

    Замена router-level `dependencies=[Depends(check_internal_token)]`:
    FastAPI резолвит `Header`-параметр напрямую (он виден и в OpenAPI,
    как раньше), проверка — та же `check_internal_token` из deps.internal.

    Return-аннотация вычисляется заранее в глобалах модуля роутера:
    у обёртки (и у inject-обёртки dishka поверх неё) `__globals__` —
    этот модуль, строковые аннотации роутера там не разрешатся.

    Args:
        endpoint: Async-функция-эндпоинт.

    Returns:
        Обёртка с KEYWORD_ONLY `x_internal_token` в сигнатуре.

    Raises:
        TypeError: если эндпоинт не async.
    """
    if not iscoroutinefunction(endpoint):
        raise TypeError("InternalDishkaRoute поддерживает только async-эндпоинты")
    sig = signature(endpoint)
    hints = get_type_hints(endpoint, include_extras=True)
    params = list(sig.parameters.values()) + [_INTERNAL_TOKEN_PARAM]
    annotations = dict(hints)
    annotations["x_internal_token"] = _INTERNAL_TOKEN_PARAM.annotation

    async def guarded(
        *args: Any, x_internal_token: str | None = None, **kwargs: Any
    ) -> Any:
        check_internal_token(x_internal_token)
        return await endpoint(*args, **kwargs)

    guarded.__annotations__ = annotations
    guarded.__signature__ = Signature(
        params, return_annotation=hints.get("return", sig.return_annotation)
    )
    return guarded


class InternalDishkaRoute(DishkaRoute):
    """DishkaRoute + обязательный `X-Internal-Token` (защита внутренних API).

    Для «внутренних» роутеров (бот/воркеры/админка): проверка токена
    выполняется до dishka-резолва сценариев. «Забыть» защиту на новом
    маршруте нельзя — она в классе, а не в списке зависимостей.
    """

    def __init__(
        self,
        path: str,
        endpoint,  # noqa: ANN001 — сигнатура FastAPI
        **kwargs,
    ) -> None:
        super().__init__(path, _guarded_internal(endpoint), **kwargs)
