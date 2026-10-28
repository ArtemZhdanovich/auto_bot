# Backend (ТЗ п. 11.3, адаптирован под uv/pyproject)
# syntax=docker/dockerfile:1
FROM python:3.13-slim

WORKDIR /app

# PIP_RETRIES/TIMEOUT: Docker Desktop-сеть обрывает большие колёса; кэш-маунт
# ниже хранит скачанные wheel'ы между сборками — код поменяли, а зависимости
# не перекачиваются заново (взялись из /root/.cache/pip за секунды).
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_DEFAULT_TIMEOUT=300 \
    PIP_RETRIES=10

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl gcc libpq-dev && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md alembic.ini ./
COPY app ./app
# Микросервисы (профиль micro): общий код — пакет app, точки входа — services/*
COPY services/__init__.py services/_base.py ./services/
COPY services/gateway ./services/gateway
COPY services/identity ./services/identity
COPY services/catalog ./services/catalog
COPY services/billing ./services/billing
COPY services/chat ./services/chat
COPY services/partners ./services/partners
COPY services/dialog ./services/dialog
COPY services/admin ./services/admin
# 5 попыток: обрыв на большом wheel — не повод начинать всё заново
RUN --mount=type=cache,target=/root/.cache/pip \
    ok=0; for i in 1 2 3 4 5; do \
      pip install . && ok=1 && break; \
      echo "pip retry $i"; sleep 20; \
    done; [ "$ok" = "1" ]

ENV PYTHONUNBUFFERED=1
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
