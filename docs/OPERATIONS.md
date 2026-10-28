# Операции: запуск, конфигурация, мониторинг

## Локальный запуск (без Docker)

```bash
cp .env.example .env             # 1. зависимости
uv sync && cd frontend && npm ci && cd ..
# 2. конфиг: BOT_TOKEN, APP_SECRET_KEY, DATABASE_URL, REDIS_URL, RABBITMQ_URL
uv run alembic upgrade head                        # 3. миграции
uv run uvicorn app.main_api:app --port 8000        # 4. backend (mono)
uv run python -m app.transport.bot                           # 5. бот (другой терминал)
uv run python -m app.infrastructure.workers.fetch  # 6. воркеры
uv run python -m app.infrastructure.workers.send
cd frontend && npm run dev                         # 7. Mini App :8080
```

## Docker Compose: поэтапные профили

`docker-compose.yml` — ступенчатые профили (детали таблицей в конце
раздела). База (postgres+redis) поднимается всегда; ядро не требует
периферии (см. Degradation ниже).

```bash
docker compose up -d                          # base: postgres+redis
docker compose --profile core up -d           # + backend(монолит)+bot+miniapp
docker compose --profile storage up -d        # + minio (фото/S3-зеркало)
docker compose --profile workers up -d        # + rabbitmq+fetch/send+translate
docker compose --profile realtime up -d       # + centrifugo+chat-сервис
docker compose --profile ai up -d             # + dialog+qdrant+rag
docker compose --profile micro up -d          # + gateway+декомпозиция сервисов
start.bat [mono|micro]                        # Windows: миграции+health+старт
```

| Ступень | Что появляется | Что деградирует без следующей |
|---|---|---|
| base | postgres, redis | — |
| core | backend (все роутеры), bot, miniapp | продукт жив: лента/поиск/тарифы/бот/рынок/чаты(поллинг); без rabbit — воркеры не крутят fetch/notify |
| storage | minio | фото-загрузки и S3-зеркало вкл (без — 503/NULL) |
| workers | rabbitmq, fetch/send, translate | свежие объявления, русские переводы |
| realtime | centrifugo, chat | мгновенные чаты/статусы, presence |
| ai | dialog, qdrant, rag | RAG-бот (LLM-fallback остаётся) |
| micro | gateway + доменные сервисы | декомпозиция вместо монолита |

⚠️ Профили API не смешивать: gateway имеет алиас `backend` — оба
API-контейнера одновременно = DNS round-robin и 502. Переключение:

```bash
docker compose rm -sf backend && COMPOSE_PROFILES=micro docker compose up -d
docker compose rm -sf gateway && COMPOSE_PROFILES=mono docker compose up -d
docker compose exec -T gateway alembic upgrade head   # миграции (или backend)
```

Degradation: Centrifugo → поллинг-фоллбэк; MinIO → 503 photos_unavailable,
raw NULL; translate → хангыль остаётся; RAG → LLM; RabbitMQ → HTTP-ядро
не зависит (depends_on required:false). Критичны только postgres+redis.

Боевое развёртывание (Caddy + Let's Encrypt, bootstrap VPS) —
[deploy/DEPLOY.md](../deploy/DEPLOY.md).

## Миниапп в Telegram (валидный HTTPS)

Telegram открывает web_app **только** по https с доверенным сертификатом.
Self-signed из `frontend/entrypoint-ssl.sh` годится для браузера на LAN
(`https://<LAN_IP>:8443`), но Telegram такой сайт молча не покажет.
Публичный адрес выдаёт SSH-туннель к localhost.run — без аккаунта:

```bat
miniapp-url.bat
```

Скрипт: поднимает `ssh -R 80:localhost:8080 nokey@localhost.run`, забирает
выданный URL (`https://<random>.lhr.life`), пишет его в `MINIAPP_URL` (.env)
и пересоздаёт бота (кнопку web_app бот ставит при старте; PID туннеля — в
`.miniapp_tunnel.pid`). Проверка руками: `curl -s https://<url>` — должен
быть 200 **без** `-k`.

Нюансы:
- URL случайный и меняется при обрыве ssh/повторном запуске — просто
  запусти скрипт ещё раз (старый процесс он гасит сам).
- Cloudflare quick-tunnel (`trycloudflare.com`) в этой сети блокируется
  провайдером (EOF на `api.trycloudflare.com`) — поэтому localhost.run.
  ngrok доступен, но требует аккаунт.
- Постоянный адрес: бесплатный аккаунт localhost.run (свой поддомен
  `name.lhr.life`) или именованный Cloudflare Tunnel со своим доменом;
  тогда туннель можно завернуть в compose-сервис и забыть про пересоздания.

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

### S3 / Centrifugo / рынок / прокси / мониторинг
- `S3_ENDPOINT_URL` (пусто = S3 выкл), `S3_ACCESS_KEY`/`S3_SECRET_KEY`,
  `S3_BUCKET=raw-payloads`, `RAW_RETENTION_DAYS=180`.
- `S3_PHOTOS_BUCKET=market-photos`, `S3_PUBLIC_URL` (публичный URL фото;
  в docker — http://localhost:9000, внутри — http://minio:9000),
  `MARKET_PHOTO_MIRROR_LIMIT=5` (зеркало фото внешних лотов),
  `MARKET_MIRROR_BATCH=40`.
- `CENTRIFUGO_URL` (внутренний, http://centrifugo:8000),
  `CENTRIFUGO_PUBLIC_URL` (ws для браузера), `CENTRIFUGO_SECRET`,
  `CENTRIFUGO_HTTP_API_KEY` — в prod не change-me (fail-fast).
- Рынок: `MARKET_FREE_DAYS=7`, `MARKET_WEEK_STARS=20`,
  `MARKET_USER_MAX_PAID_LOTS=3`, `MARKET_FREE_COOLDOWN_DAYS=30`,
  `MARKET_PARTNER_PACKAGES` (JSON {"10":500,…}),
  `MARKET_FORBIDDEN_WORDS` (список для автомодерации).
- `PROXY_DATAIMPULSE_USER`+`PASS` — прокси для источников с
  `capabilities.use_proxy=true`; `STICKY_TTL_SEC=1800`.
- `PARTNER_RATE_LIMIT_PER_MIN=60`; `PARTNER_CPL_RATES`.
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
