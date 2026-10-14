# Backend (ТЗ п. 11.3, адаптирован под uv/pyproject)
FROM python:3.13-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl gcc libpq-dev && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY app ./app
# Микросервисы (профиль micro): общий код — пакет app, точки входа — services/*
COPY services/__init__.py services/_base.py ./services/
COPY services/gateway ./services/gateway
COPY services/identity ./services/identity
COPY services/catalog ./services/catalog
COPY services/billing ./services/billing
COPY services/partners ./services/partners
COPY services/dialog ./services/dialog
COPY services/admin ./services/admin
RUN pip install --no-cache-dir .

ENV PYTHONUNBUFFERED=1
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
