# syntax=docker/dockerfile:1
# RAG-микросервис auto_bot (worker + api в одном образе, команда выбирается в compose).

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

WORKDIR /app
# UV_HTTP_TIMEOUT: тяжёлые колёса (torch ~190МБ) рвутся на медленной сети;
# кэш-маунт + retry-цикл копят прогресс между попытками.
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_HTTP_TIMEOUT=600

COPY pyproject.toml uv.lock ./
# 5 попыток: обрыв соединения на большом wheel — не повод начинывать всё заново
RUN --mount=type=cache,target=/root/.cache/uv \
    ok=0; for i in 1 2 3 4 5; do \
      uv sync --frozen --no-install-project --no-dev && ok=1 && break; \
      echo "uv retry $i"; sleep 20; \
    done; [ "$ok" = "1" ]
COPY rag_core ./rag_core
COPY rag_worker ./rag_worker
COPY rag_api ./rag_api
COPY alembic.ini ./
COPY migrations ./migrations
RUN uv sync --frozen --no-dev


FROM python:3.12-slim-bookworm

RUN groupadd --system app && useradd --system --gid app --create-home app

WORKDIR /app
COPY --from=builder --chown=app:app /app/.venv /app/.venv
COPY --from=builder --chown=app:app /app/alembic.ini ./
COPY --from=builder --chown=app:app /app/migrations ./migrations

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

USER app
