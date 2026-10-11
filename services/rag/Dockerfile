# syntax=docker/dockerfile:1
# RAG-микросервис auto_bot (worker + api в одном образе, команда выбирается в compose).

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

COPY pyproject.toml ./
RUN uv sync --frozen --no-install-project --no-dev
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
