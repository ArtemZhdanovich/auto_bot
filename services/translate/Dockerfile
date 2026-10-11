# Translate API — NLLB-перевод тайтлов (PLAN.md, фаза 5).
# torch берём CPU-сборку: инференс 600M-модели на CPU укладывается в бюджет,
# а образ без CUDA на ~2 ГБ легче.
FROM python:3.12-slim

WORKDIR /srv
ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HF_HOME=/srv/.hf

COPY pyproject.toml ./
COPY translate_api ./translate_api
RUN pip install --extra-index-url https://download.pytorch.org/whl/cpu .

EXPOSE 8200
CMD ["translate-api"]