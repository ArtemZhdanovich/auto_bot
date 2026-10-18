# syntax=docker/dockerfile:1
# Translate API — NLLB-перевод тайтлов (PLAN.md, фаза 5).
# torch берём CPU-сборку: инференс 600M-модели на CPU укладывается в бюджет,
# а образ без CUDA на ~2 ГБ легче.
FROM python:3.12-slim

WORKDIR /srv
# PIP_DEFAULT_TIMEOUT/RETRIES: сеть отдаёт большие колёса (torch ~190МБ) с
# обрывами; кэш-маунт + retry-цикл в RUN — докачка не теряется между попытками.
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_DEFAULT_TIMEOUT=300 \
    PIP_RETRIES=10 \
    HF_HOME=/srv/.hf

COPY pyproject.toml ./
COPY translate_api ./translate_api
# 5 попыток: обрыв соединения на большом wheel — не повод начинывать всё заново
RUN --mount=type=cache,target=/root/.cache/pip \
    ok=0; for i in 1 2 3 4 5; do \
      pip install --extra-index-url https://download.pytorch.org/whl/cpu . \
        && ok=1 && break; \
      echo "pip retry $i"; sleep 20; \
    done; [ "$ok" = "1" ]

EXPOSE 8200
CMD ["translate-api"]