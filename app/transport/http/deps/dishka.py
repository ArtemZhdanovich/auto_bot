"""DishkaRoute с разрешением return-аннотации (future annotations).

`inject` из dishka компилирует новую функцию-обёртку: её `__globals__` —
не модуль роутера, поэтому FastAPI не может вычислить строковую
return-аннотацию (`from __future__ import annotations`) и строит
response-модель от неразрешённого `ForwardRef`. Здесь сигнатура обёртки
пересобирается с уже вычисленным типом возврата (глобалы оригинала).
"""

from __future__ import annotations

from inspect import signature
from typing import get_type_hints

from dishka.integrations.fastapi import inject
from fastapi.routing import APIRoute


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
