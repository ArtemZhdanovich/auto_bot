"""Translate API — лёгкий HTTP-сервис перевода тайтлов (PLAN.md, фаза 5).

Optional-сервис: бэкенд включен флагом TRANSLATE_ENABLED + TRANSLATE_SERVICE_URL;
аутентификация — Bearer API_TOKEN (если задан; иначе эндпоинт открыт в
доверенной сети). Модель грузится и прогревается на старте (иначе первый
батч после рестарта бэкенд перевёл бы с fallback на оригинал); инференс —
в threadpool, т.к. transformers синхронный.

    POST /translate {"texts": [...], "source_lang": "auto", "target_lang": "rus_Cyrl"}
    → {"translations": ["...", null, ...]}   # null = не удалось перевести
    GET  /health → {"status": "ok", "model": "...", "loaded": true}
"""

import os
from contextlib import asynccontextmanager
from typing import Any

import anyio
from fastapi import FastAPI, Header, HTTPException, status
from pydantic import BaseModel, Field

from translate_api.nllb import NLLBTranslator

MODEL_NAME = os.getenv("TRANSLATE_MODEL", "facebook/nllb-200-distilled-600M")
API_TOKEN = os.getenv("API_TOKEN", "")
WARMUP = os.getenv("TRANSLATE_WARMUP", "1").lower() not in {"0", "false", "no"}

translator = NLLBTranslator(MODEL_NAME)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    if WARMUP:
        await anyio.to_thread.run_sync(translator.warmup)
    yield


app = FastAPI(title="translate-service", lifespan=_lifespan)


class TranslateRequest(BaseModel):
    texts: list[str] = Field(min_length=1, max_length=64)
    source_lang: str = "auto"  # NLLB-код или "auto" (детект по письменности)
    target_lang: str = "rus_Cyrl"


class TranslateResponse(BaseModel):
    translations: list[str | None]


def _check_token(authorization: str | None) -> None:
    if API_TOKEN and authorization != f"Bearer {API_TOKEN}":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid token"
        )


@app.get("/health")
async def health() -> dict[str, Any]:
    return {"status": "ok", "model": MODEL_NAME, "loaded": translator.loaded}


@app.post("/translate", response_model=TranslateResponse)
async def translate(
    req: TranslateRequest, authorization: str | None = Header(default=None)
) -> TranslateResponse:
    _check_token(authorization)
    translations = await anyio.to_thread.run_sync(
        lambda: translator.translate_batch(
            req.texts, req.source_lang, req.target_lang
        )
    )
    return TranslateResponse(translations=translations)


def serve() -> None:
    import uvicorn  # noqa: PLC0415

    uvicorn.run(
        app, host="0.0.0.0", port=int(os.getenv("PORT", "8200")), log_level="info"
    )


if __name__ == "__main__":
    serve()