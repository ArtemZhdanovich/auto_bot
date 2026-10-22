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
docker compose up -d                     # профиль из COMPOSE_PROFILES (.env; по умолчанию micro)
docker compose --profile mono up -d      # монолит
docker compose --profile micro up -d     # gateway + доменные сервисы
start.bat [mono|micro]                   # то же + миграции + ожидание health (Windows);
                                         # по умолчанию micro, при переключении сам
                                         # прибивает API-контейнер чужого профиля
```

Оба профиля работают ОДИНАКОВО: API на хосте всегда `:8000` (micro — gateway,
mono — backend; порт общий, поэтому оба API-контейнера одновременно жить
не могут), сайт на `:8080`/`:8443`, миграции:
`docker compose exec -T gateway alembic upgrade head` (micro) или
`docker compose exec -T backend alembic upgrade head` (mono).

Горячее переключение профиля (без остановки остального стека):

```bash
# mono -> micro: прибить backend, поднять gateway (compose пересоздаст
# только то, чей конфиг разнится с файлом)
docker compose rm -sf backend && COMPOSE_PROFILES=micro docker compose up -d
# micro -> mono
docker compose rm -sf gateway && COMPOSE_PROFILES=mono docker compose up -d
# миграции из нового API-сервиса
docker compose exec -T gateway alembic upgrade head   # (или backend)
```

`start.bat [mono|micro]` делает всё это автоматически.

⚠️ Профили не смешивать: gateway имеет сетевой алиас `backend` — при двух
API-контейнерах одновременно DNS отдаёт два IP и половина запросов ловит 502
(DNS round-robin). CLI-флаг `--profile` ОБЪЕДИНЯЕТСЯ с `COMPOSE_PROFILES`
из `.env` — переключать профиль меняйте переменную в `.env`, запускайте
`start.bat` или используйте команды горячего переключения выше (они гасят
чужой API-контейнер перед подъёмом).

Боевой развёртывание (Caddy + Let's Encrypt, bootstrap VPS) —
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
