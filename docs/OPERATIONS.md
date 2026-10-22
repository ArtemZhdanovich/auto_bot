# Операции: запуск, конфигурация, мониторинг

## Локальный запуск (без Docker)

```bash
cp .env.example .env             # 1. зависимости
uv sync && cd frontend && npm ci && cd ..
# 2. конфиг: BOT_TOKEN, APP_SECRET_KEY, DATABASE_URL, REDIS_URL, RABBITMQ_URL
uv run alembic upgrade head                        # 3. миграции
uv run uvicorn app.main_api:app --port 8000        # 4. backend (mono)
uv run python -m app.bot                           # 5. бот (другой терминал)
uv run python -m app.infrastructure.workers.fetch  # 6. воркеры
uv run python -m app.infrastructure.workers.send
cd frontend && npm run dev                         # 7. Mini App :8080
```

## Docker Compose

`docker-compose.yml` — вся инфраструктура: postgres, redis, rabbitmq, backend
(mono, :8000), bot, worker_fetch, worker_send, miniapp (:8080/:8443),
gateway+6 сервисов (профиль micro), qdrant, minio, rag (api/worker/миграции),
translate.

```bash
docker compose up -d                     # профиль из COMPOSE_PROFILES (.env; по умолчанию mono)
docker compose --profile mono up -d      # монолит
docker compose --profile micro up -d     # gateway + доменные сервисы
start.bat [mono|micro]                   # то же + миграции + ожидание health (Windows)
```

Оба профиля работают ОДИНАКОВО: API на хосте всегда `:8000` (mono — backend,
micro — gateway; порт общий, поэтому оба API-контейнера одновременно жить
не могут), сайт на `:8080`/`:8443`, миграции:
`docker compose exec -T backend alembic upgrade head` (mono) или
`docker compose exec -T gateway alembic upgrade head` (micro).

⚠️ Профили не смешивать: gateway имеет сетевой алиас `backend` — при двух
профилях одновременно DNS отдаёт два IP и половина запросов ловит 502
(DNS round-robin). CLI-флаг `--profile` ОБЪЕДИНЯЕТСЯ с `COMPOSE_PROFILES`
из `.env` — переключать профиль меняйте переменную в `.env` или запускайте
через `start.bat` (он выставляет `COMPOSE_PROFILES` окружением, что имеет
приоритет). Погасить чужой API-контейнер: `docker compose stop gateway`.

Боевой развёртывание (Caddy + Let's Encrypt, bootstrap VPS) —
[deploy/DEPLOY.md](../deploy/DEPLOY.md).

## Конфигурация (.env)

Полное зеркало `Settings` — см. `.env.example` (комментарии по каждому блоку).
Ключевые группы:

### Обязательный минимум
- `BOT_TOKEN` — токен бота (@BotFather).
- `APP_SECRET_KEY` — подпись JWT; при `APP_ENV=prod` обязан быть ≥ 32 символов
  и не дев-дефолт (fail-fast, `tests/security/test_prod_secrets.py`).
- `DATABASE_URL`, `REDIS_URL`, `RABBITMQ_URL`.

### Авторизация
- `INTERNAL_TOKEN` — отдельный секрет админ/internal-эндпоинтов; пусто =
  `APP_SECRET_KEY` (совместимость, в бою задавать явно).
- `ACCESS_TOKEN_TTL_SEC=3600`, `REFRESH_TOKEN_TTL_SEC=2592000`,
  `INIT_DATA_MAX_AGE_SEC=86400` — initData старше суток отклоняется.

### LLM / RAG / перевод
- `LLM_PROVIDER`: `glm` | `anthropic` | `ollama` | `mock`; `GLM_API_KEY`,
  `GLM_BASE_URL` (по умолчанию `https://api.z.ai/api/paas/v4`).
- `RAG_SERVICE_URL` (пусто = RAG выключен), `RAG_ENABLED`, `RAG_API_TOKEN`,
  `RAG_TIMEOUT_SEC=5`, `RAG_CACHE_TTL_SEC=3600`, `RAG_CONFIDENCE_THRESHOLD=0.6`.
- `TRANSLATE_ENABLED=false` по умолчанию; `TRANSLATE_SOURCE_LANGS` — JSON-карта
  источник→NLLB-код.

### Уведомления
- `NOTIFY_RATE_LIMIT_SEC=30` — не чаще 1 сообщения/30 с на юзера.
- `TELEGRAM_RATE_GLOBAL_PER_SEC=30` / `TELEGRAM_RATE_CHAT_PER_SEC=1`
  (0 = выкл; Redis лежит → fail-open).
- `NOTIFY_SENT_TTL_SEC` — 7 дней антидубля; `NOTIFY_QUIET_HOURS_DEFAULT` —
  тихие часы `"23:00-08:00"`; `STALE_MAX_AGE_DAYS=3`.

### S3 / прокси / партнёры / мониторинг
- `RAW_PAYLOAD_ENABLED`, `S3_BUCKET=raw-payloads`, `RAW_RETENTION_DAYS=180`,
  `RAW_PRESIGN_TTL_SEC=600`.
- `PROXY_DATAIMPULSE_USER`+`PASS` — прокси для источников с
  `capabilities.use_proxy=true`; `STICKY_TTL_SEC=1800`.
- `PARTNER_RATE_LIMIT_PER_MIN=60`; `PARTNER_CPL_RATES` — CPL-тарифы планов.
- `ALERTS_ENABLED=true`, `ALERT_DEDUP_TTL_SEC=1800`, `DLQ_ALERT_THRESHOLD=10`,
  `SENTRY_DSN`, `LOG_LEVEL`.
- `ENCRYPT_DOCUMENTS` + `PQ_ENCRYPTION_PUBLIC_KEY/PRIVATE_KEY` — PQ-шифрование
  документов RAG.

## Мониторинг и алерты

- **Health**: `/health` (liveness), `/health/detailed` (postgres/redis/
  rabbitmq; 503 только если лежит БД).
- **Метрики**: `/metrics` (X-Internal-Token) — юзеры/фильтры/объявления/
  уведомления 24 ч, тарифы, health источников, события 24 ч.
- **Health источников** (`source_health`): avg latency, error_streak;
  circuit breaker: 5 ошибок подряд → источник временно отключается.
- **Алерты**: Telegram на `ADMIN_CHAT_ID` с дедупом `alert:{key}` (TTL 30 мин).
  Алертятся: DLQ ≥ 10, поломка парсера, отключение источника CB, открытие CB
  перевода/прокси/RAG, партнёр приостановлен за неуплату.
- **ParserDoctor** (`doctor_mode`): `off` / `suggest` / `auto` — при поломке
  селекторов GLM предлагает ремонт.

## Прод-чеклист

1. Секреты: `APP_SECRET_KEY` ≥ 32 символов, отдельный `INTERNAL_TOKEN`,
   свежий `BOT_TOKEN`.
2. Миграции на копии БД: `upgrade head` → `downgrade -1` → `upgrade head`.
3. `APP_ENV=prod` — конфиг падает на старте при слабых секретах.
4. TLS: Caddy + Let's Encrypt (`deploy/docker-compose.prod.yml`), не
   self-signed.
5. `ALERTS_ENABLED=true` и доступный `ADMIN_CHAT_ID`.
6. Политика конфиденциальности: тексты диалогов уходят во внешний LLM
   (см. INC-003 в [INCIDENTS.md](../INCIDENTS.md)).
