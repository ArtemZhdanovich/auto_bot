# Операции: dev-цикл, Docker Compose, мониторинг, бэкапы

Единая страница эксплуатации: требования, установка, конфигурация,
ступенчатый запуск, логи/метрики, обновление, бэкапы. Инцидент-рецепты —
в [RUNBOOK.md](RUNBOOK.md), безопасность — в [SECURITY.md](SECURITY.md),
тесты — в [TESTING.md](TESTING.md).

## 1. Требования

| Компонент | Версия | Зачем |
|---|---|---|
| Python | 3.13 (`requires-python = ">=3.13"`, [pyproject.toml](../pyproject.toml)) | бэкенд, воркеры, бот |
| uv | актуальный | менеджер Python-зависимостей (`uv sync`, `uv run`) |
| Node.js / npm | для Vite 6 / Vitest 2 (frontend/package.json) | Mini App (React 18 + TS) |
| Docker Desktop / Engine + compose v2 | compose-файл использует `depends_on.condition` и profiles | весь стек |
| PostgreSQL | 16 (образ `postgres:16`) | основная БД |
| Redis | 7 (образ `redis:7-alpine`) | дедуп, rate-limits, кэш, FSM бота, DialogState |
| RabbitMQ | 3-management | очереди задач (5672) + UI (15672) |
| Centrifugo | v6 (образ `centrifugo/centrifugo:v6`) | realtime чаты/присутствие |
| MinIO | `${MINIO_IMAGE:-bitnamilegacy/minio:latest}` | S3: raw-payload + фото рынка |
| Qdrant | latest, `mem_limit 2g` | векторная БД RAG |

Порты, которые слушает стек (хост): `5432` postgres, `6379` redis,
`5672`/`15672` rabbitmq, `8000` backend/gateway, `8008` centrifugo,
`8080`/`8443` miniapp, `9000`/`9001` minio, `6333` qdrant, `8100` rag_api,
`8101–8107` доменные сервисы (профиль micro), `8200` translate.

## 2. Установка (dev, без Docker)

```bash
git clone <repo> && cd auto_bot
cp .env.example .env          # минимальные правки: BOT_TOKEN, GLM_API_KEY
uv sync                       # python-зависимости (.venv)
cd frontend && npm ci && cd . # frontend-зависимости
uv run alembic upgrade head   # миграции (нужен поднятый postgres)
```

Запуск процессов (каждый в своём терминале):

```bash
uv run uvicorn app.main_api:app --port 8000          # backend-монолит
uv run python -m app.transport.bot                    # бот (long polling)
uv run python -m app.infrastructure.workers.fetch     # fetch-воркер
uv run python -m app.infrastructure.workers.send      # send-воркер
cd frontend && npm run dev                            # Mini App :8080 (/api → :8000)
```

`app/main.py` — совместимый entrypoint (`uvicorn app.main:app`), реальное
приложение собирается в [app/main_api.py](../app/main_api.py).

## 3. Переменные окружения (.env)

Источник истины — `Settings` в [app/config.py](../app/config.py)
(pydantic-settings: имя поля → `UPPER_SNAKE` env) + интерполяция
`environment:` в [docker-compose.yml](../docker-compose.yml). Комментарии
по блокам — в [.env.example](../.env.example). Секреты ниже — плейсхолдеры
`<secret>`; в проде `APP_ENV=prod` включает fail-fast: дев-дефолт или
короткий (<32) `APP_SECRET_KEY`, пустой `INTERNAL_TOKEN`, `change-me`
у Centrifugo, дефолтные/`guest:guest` URL БД/брокера — старт падает
(`Settings._prod_secrets_required`).

### 3.1 App / Bot

| Переменная | Дефолт | Куда | Зачем |
|---|---|---|---|
| `APP_ENV` | `dev` | все python-процессы | `dev \| test \| staging \| prod`; `prod` включает fail-fast секретов (`production` — алиас) |
| `APP_SECRET_KEY` | дев-дефолт (запрещён в prod) | все | подпись JWT (HS256); в dev fallback для internal-API. `<secret>` ≥32 симв. |
| `INTERNAL_TOKEN` | `""` | backend, воркеры, centrifugo (sed-подстановка) | `X-Internal-Token` для /api/admin, /metrics, бота, subscribe-proxy; в prod обязателен. `<secret>` |
| `APP_BASE_URL` | `http://localhost:8000` | бот | куда бот ходит за API (в compose: `http://backend:8000`) |
| `MINIAPP_URL` | `http://localhost:8080` | бот | URL web_app-кнопки (см. §6) |
| `BOT_TOKEN` | `""` | бот, send-воркер | токен @BotFather. `<secret>` |
| `BOT_USERNAME` | `""` | backend | юзернейм без `@` для «Войти через Telegram»; пусто → кнопка 503 |
| `ADMIN_CHAT_ID` | — | воркеры | получатель алертов; пусто = алерты только в лог |
| `MANAGER_CHAT_ID` | — | воркеры | fallback-получатель алертов |

### 3.2 Инфраструктура

| Переменная | Дефолт | Куда | Зачем |
|---|---|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://app:app@localhost:5432/autoselect` | все | Postgres (SQLAlchemy async). В compose перекрывается на хост `postgres` |
| `DB_PASSWORD` | `app` | docker-compose (postgres) | `POSTGRES_PASSWORD`; менять вместе с `DATABASE_URL`. `<secret>` |
| `REDIS_URL` | `redis://localhost:6379/0` | все | Redis (db 0 — приложение; db 1 — rag_worker) |
| `RABBITMQ_URL` | `amqp://guest:guest@localhost:5672/` | backend, воркеры, rag | RabbitMQ; в prod `guest:guest` запрещён fail-fast'ом. `<secret>` |

### 3.3 Auth / партнёры

| Переменная | Дефолт | Куда | Зачем |
|---|---|---|---|
| `JWT_ALGORITHM` | `HS256` | backend | алгоритм подписи JWT (HS256/384/512) |
| `ACCESS_TOKEN_TTL_SEC` | `3600` | backend | жизнь access-JWT |
| `REFRESH_TOKEN_TTL_SEC` | `2592000` (30 д) | backend | жизнь refresh-JWT (валидатор: > access) |
| `INIT_DATA_MAX_AGE_SEC` | `86400` (24 ч) | backend | максимум возраста Telegram initData |
| `GUEST_IP_RATE_LIMIT` | `5` | backend | гостевых сессий с IP в минуту (429 сверх) |
| `PARTNER_RATE_LIMIT_PER_MIN` | `60` | backend | запросов/мин на партнёрский API-ключ |
| `PARTNER_CPL_RATES` | `{"starter": 5.0, "pro": 3.0, "salon": 2.0}` | backend | CPL-тарифы USD/лид по планам (JSON) |

### 3.4 LLM / диалог / RAG

| Переменная | Дефолт | Куда | Зачем |
|---|---|---|---|
| `LLM_PROVIDER` | `glm` | backend | `glm \| anthropic \| ollama \| mock` |
| `GLM_API_KEY` | — | backend, rag_worker | ключ Z.ai/bigmodel. `<secret>` |
| `GLM_BASE_URL` | `https://api.z.ai/api/paas/v4` | backend, rag | для ключей open.bigmodel.cn — `https://open.bigmodel.cn/api/paas/v4` |
| `ANTHROPIC_API_KEY` | — | backend | альтернативный провайдер. `<secret>` |
| `LLM_DEFAULT_MODEL` / `LLM_ADVANCED_MODEL` | `glm-4.6` | backend | модели обычных/продвинутых задач |
| `LLM_BASE_URL` | — | backend | переопределение endpoint (Ollama/прокси) |
| `LLM_TEMPERATURE` | `0.2` | backend | температура генерации |
| `DIALOG_TTL_SEC` | `86400` | backend | жизнь диалога (24 ч) |
| `DIALOG_HISTORY_LEN` | `8` | backend | последних N сообщений в контексте |
| `FAQ_CACHE_TTL_SEC` | `86400` | backend | кэш FAQ-ответов в Redis |
| `RAG_SERVICE_URL` | — (выкл) | backend, chat | адрес rag_api; пусто = RAG выключен (LLM-fallback) |
| `RAG_API_TOKEN` | — | backend | Bearer к rag_api. `<secret>` |
| `RAG_ENABLED` | `true` | backend | feature flag RAG |
| `RAG_TIMEOUT_SEC` | `5` | backend | таймаут запроса к RAG (в compose для backend — 120: GLM отвечает 60–90 с) |
| `RAG_CACHE_TTL_SEC` | `3600` | backend | кэш ответов RAG в Redis |
| `RAG_CONFIDENCE_THRESHOLD` | `0.6` | backend | ниже — fallback на LLM |
| `RAG_CB_FAIL_THRESHOLD` | `5` | backend | circuit breaker RAG |
| `RAG_CB_COOLDOWN_SEC` | `300` | backend | пауза CB RAG |

Микросервис RAG (`services/rag`, читает docker-compose.yml):

| Переменная | Дефолт | Куда | Зачем |
|---|---|---|---|
| `RAG_JWT_SECRET` | `APP_SECRET_KEY` | rag_api/worker | подпись внутренних JWT. `<secret>` |
| `RAG_EMBEDDING_MODEL` | `intfloat/multilingual-e5-small` | rag_worker | модель эмбеддингов |
| `RAG_LLM_PROVIDER` / `RAG_LLM_MODEL` | `glm` / `glm-4.6` | rag_worker | LLM воркера |
| `DEEPSEEK_API_KEY` | — | rag_worker | альтернативная LLM. `<secret>` |
| `ENCRYPT_DOCUMENTS` | `false` | rag_worker | PQ-шифрование чанков at-rest |
| `PQ_ENCRYPTION_PUBLIC_KEY` / `PQ_ENCRYPTION_PRIVATE_KEY` | — | rag_worker | ключи ML-KEM-768+X25519 (генерация — см. .env.example). `<secret>` |

### 3.5 Уведомления / перевод / stale

| Переменная | Дефолт | Куда | Зачем |
|---|---|---|---|
| `NOTIFY_RATE_LIMIT_SEC` | `30` | send-воркер | ≥1 сообщения/30 с на юзера |
| `TELEGRAM_RATE_GLOBAL_PER_SEC` | `30` | send-воркер | token bucket Bot API (0 = выкл; Redis лежит → fail-open) |
| `TELEGRAM_RATE_CHAT_PER_SEC` | `1` | send-воркер | bucket на чат |
| `NOTIFY_SCAN_WINDOW_SEC` | `1800` | send-воркер | окно «свежести» объявлений для скана фильтров |
| `NOTIFY_SENT_TTL_SEC` | `604800` | send-воркер | антидубль `notified:{uid}:{lid}` (7 д) |
| `DIGEST_MAX_CARDS` | `5` | send-воркер | карточек в дайджесте (1–10) |
| `NOTIFY_QUIET_HOURS_DEFAULT` | — | send-воркер | тихие часы `"23:00-08:00"` |
| `STALE_MAX_AGE_DAYS` | `3` | fetch-воркер | не виден N дней → `is_active=false` (stale_check_job) |
| `TRANSLATE_ENABLED` | `false` | backend | флаг перевода тайтлов (выкл → оригинал) |
| `TRANSLATE_SERVICE_URL` | `""` | backend | адрес services/translate (`http://translate:8200`) |
| `TRANSLATE_API_TOKEN` | — | backend + контейнер translate | Bearer `API_TOKEN`. `<secret>` |
| `TRANSLATE_TIMEOUT_SEC` | `180` | backend | NLLB на CPU: инференс сериализован локом |
| `TRANSLATE_BATCH_SIZE` | `32` | backend | batchSize перевода (чанк enqueue — min(16, max(8, N))) |
| `TRANSLATE_CACHE_TTL_SEC` | `2592000` | backend | кэш `tr:{tgt}:{src}:{sha1}` (30 д) |
| `TRANSLATE_CB_FAIL_THRESHOLD` | `3` | backend | CB перевода |
| `TRANSLATE_CB_COOLDOWN_SEC` | `300` | backend | пауза CB |
| `TRANSLATE_TARGET_LANG` | `rus_Cyrl` | backend | NLLB-код цели |
| `TRANSLATE_SOURCE_LANGS` | `{"encar": "kor_Hang", "kcar": "kor_Hang", "che168": "zho_Hans"}` | backend | источник → NLLB-код ("auto" — детект) |
| `TRANSLATE_MODEL` | `facebook/nllb-200-distilled-600M` | контейнер translate | модель NLLB |
| `TRANSLATE_MAX_RSS_MB` | `2950` (compose) | контейнер translate | RSS-сторож: выше — self-exit → рестарт (мем-лимит 3500m) |

### 3.6 S3 / MinIO

| Переменная | Дефолт | Куда | Зачем |
|---|---|---|---|
| `RAW_PAYLOAD_ENABLED` | `false` | fetch-воркер | JSON сырых ответов источников → S3 |
| `S3_ENDPOINT_URL` | `""` (выкл) | backend, fetch | `http://minio:9000` в compose, `http://localhost:9000` локально |
| `S3_BUCKET` | `raw-payloads` | fetch | bucket raw-payload |
| `S3_ACCESS_KEY` / `S3_SECRET_KEY` | `""` | backend, fetch | = `MINIO_ROOT_USER/PASSWORD` (`minioadmin` в dev). `<secret>` |
| `S3_REGION` | `us-east-1` | все S3-клиенты | регион |
| `RAW_RETENTION_DAYS` | `180` | fetch-воркер | retention-крон raw-объектов |
| `RAW_PRESIGN_TTL_SEC` | `600` | backend | presigned GET для админки |
| `S3_PHOTOS_BUCKET` | `market-photos` | backend | bucket фото рынка (public-read) |
| `S3_PUBLIC_URL` | `http://localhost:9000` | backend | адрес MinIO для браузера (не `minio:9000`) |
| `MARKET_PHOTO_MIRROR_LIMIT` | `5` | fetch-воркер | фото внешнего лота в зеркало (0 = выкл) |
| `MARKET_MIRROR_BATCH` | `40` | fetch-воркер | лотов за прогон mirror_photos_task |
| `MINIO_IMAGE` | `bitnamilegacy/minio:latest` | docker-compose | образ MinIO (minio/minio удалён из Docker Hub) |

### 3.7 Прокси DataImpulse (парсеры)

| Переменная | Дефолт | Куда | Зачем |
|---|---|---|---|
| `PROXY_DATAIMPULSE_USER` / `PASS` | — | fetch-воркер | пара: обе или ни одной (валидатор). `<secret>` |
| `PROXY_DATAIMPULSE_HOST` | `gw.dataimpulse.com` | fetch | шлюз |
| `PROXY_DATAIMPULSE_PORT` | `823` | fetch | 823 HTTPS / 824 SOCKS5 |
| `PROXY_DATAIMPULSE_GEO` | — | fetch | страна выхода (`cn`, `cn,de`) |
| `PROXY_DATAIMPULSE_STICKY_TTL_SEC` | `1800` | fetch | sticky-окно IP (0 = ротация на запрос) |
| `PROXY_DATAIMPULSE_FAIL_THRESHOLD` | `5` | fetch | ConnectError подряд → пауза шлюза |
| `PROXY_DATAIMPULSE_FAIL_COOLDOWN_SEC` | `300` | fetch | пауза (ходим напрямую) |

### 3.8 Микросервисы / Centrifugo / рынок

| Переменная | Дефолт | Куда | Зачем |
|---|---|---|---|
| `GATEWAY_TIMEOUT_SEC` | `150` | gateway | таймаут прокси (analysis гоняет LLM до 120 с) |
| `SVC_IDENTITY_URL` … `SVC_CHAT_URL` | `http://localhost:8101…8107` | gateway | адреса доменных сервисов (в compose — `http://identity:8101` и т.д.) |
| `CENTRIFUGO_URL` | `http://localhost:8008` | backend, chat | publish-API из контейнеров (в compose `http://centrifugo:8000`) |
| `CENTRIFUGO_PUBLIC_URL` | `""` | backend | ws-endpoint для браузера (отдаётся в /api/chats/ws-token) |
| `CENTRIFUGO_SECRET` | `change-me` | backend, chat, контейнер centrifugo | подпись браузерных connection-JWT. `<secret>` |
| `CENTRIFUGO_HTTP_API_KEY` | `change-me` | backend, chat, centrifugo | ключ publish-API + subscribe-proxy. `<secret>` |
| `MARKET_FREE_DAYS` | `7` | backend | бесплатный период размещения лота |
| `MARKET_WEEK_STARS` | `20` | backend | цена недели в Stars (4 нед = ×3) |
| `MARKET_USER_MAX_PAID_LOTS` | `3` | backend | платных лотов сверх включённого |
| `MARKET_FREE_COOLDOWN_DAYS` | `30` | backend | кулдаун free-периода (анти-ротация) |
| `MARKET_PARTNER_PACKAGES` | `{"10": 500, "50": 1800, "200": 5000}` | backend | пакеты размещений {шт: руб} |
| `MARKET_FORBIDDEN_WORDS` | список (авито, drom, крипта…) | backend | авто-модерация лотов, подстрока без регистра |
| `USE_CASE_FAVORITES` | `true` | backend | toggle избранного через use case |

### 3.9 Алерты / мониторинг / прочее

| Переменная | Дефолт | Куда | Зачем |
|---|---|---|---|
| `ALERTS_ENABLED` | `true` | воркеры | алерты в Telegram на `ADMIN_CHAT_ID` |
| `ALERT_DEDUP_TTL_SEC` | `1800` | воркеры | дедуп `alert:{key}` |
| `DLQ_ALERT_THRESHOLD` | `10` | fetch-воркер | глубина dead_letter для алерта |
| `HEALTH_CHECK_INTERVAL_SEC` | `300` | fetch-воркер | период мониторинга DLQ |
| `SOURCE_CB_ERROR_STREAK` | `5` | fetch-воркер | ошибок подряд до CB источника |
| `SENTRY_DSN` | — | (зарезервировано) | Sentry |
| `LOG_LEVEL` | `INFO` | все | DEBUG/INFO/WARNING/ERROR/CRITICAL |
| `LAN_IP` | `127.0.0.1` | docker-compose (miniapp) | SAN self-signed-сертификата |
| `HEARTBEAT_INTERVAL_SEC` / `HEARTBEAT_MAX_AGE_SEC` | `15` / `90` | bot, воркеры | heartbeat-файл liveness ([app/infrastructure/runtime/heartbeat.py](../app/infrastructure/runtime/heartbeat.py)) |
| `TEST_DATABASE_URL` | `<database>_test` | pytest | отдельная тестовая БД |

## 4. Docker Compose: ступенчатые профили

[docker-compose.yml](../docker-compose.yml): инфраструктура поднимается
ступенями через `--profile`; все `depends_on` периферии —
`required: false`, поэтому ядро стартует без rabbitmq/minio/centrifugo и
деградирует, а не падает. У всех long-running сервисов healthcheck.

| Профиль | Сервисы | Что даёт |
|---|---|---|
| (без профиля) | postgres, redis | база — поднимается всегда |
| `core` | backend (`app.main:app`:8000), bot, miniapp | весь продукт: лента/поиск/тарифы/рынок/чаты-поллинг/бот |
| `storage` | minio | S3: фото лотов + raw-payload + зеркало |
| `workers` | rabbitmq, worker_fetch, worker_send, translate | парсинг по cron, уведомления, дайджесты, перевод |
| `realtime` | centrifugo, chat (:8107) | мгновенные чаты, typing, presence |
| `ai` | dialog (:8105), qdrant, rag_api (:8100), rag_worker, rag_migrate*, | RAG-контур (без него — LLM-fallback) |
| `micro` | gateway (:8000, **алиас `backend`**) + identity 8101 / catalog 8102 / billing 8103 / partners 8104 / dialog 8105 / admin 8106 / chat 8107 | декомпозиция вместо монолита |

Дополнительно `mono`/`micro` включают rabbitmq+воркеры+minio+translate
(+dialog/qdrant/rag в `micro`), чтобы один профиль поднимал полный стек.
`rag_migrate`/`rag_migrate_alembic` — one-shot (`restart: "no"`): создают
БД `rag` и гонят её миграции.

### 4.1 Пошаговый подъём и проверка

```bash
docker compose up -d                                   # 1. base: postgres+redis
docker compose --profile core up -d                    # 2. ядро: backend+bot+miniapp
docker compose --profile storage up -d                 # 3. minio
docker compose --profile workers up -d                 # 4. rabbitmq+воркеры+translate
docker compose --profile realtime up -d                # 5. centrifugo+chat
docker compose --profile ai up -d                      # 6. RAG-контур
docker compose --profile micro up -d                   # 7. (альтернатива core) gateway+сервисы
```

Проверка после каждого шага (см. свод health-эндпоинтов в §4.2):

```bash
# шаг 1 — база
docker compose ps postgres redis          # оба healthy
docker compose exec postgres pg_isready -U app

# шаг 2 — ядро: миграции и health
docker compose exec -T backend alembic upgrade head
curl -s http://localhost:8000/health                      # {"status":"ok","env":...}
curl -s http://localhost:8000/health/detailed             # postgres/redis/rabbitmq
curl -s http://localhost:8080/                            # 200, HTML миниаппа
docker compose logs bot | grep -i "start"                 # бот запустил polling

# шаг 3 — S3
curl -s http://localhost:9000/minio/health/live           # 200
docker compose logs worker_fetch | grep -i bucket         # ensure_bucket (lazy)

# шаг 4 — очередь
curl -s http://localhost:15672                            # management UI (guest/guest в dev)
docker compose exec rabbitmq rabbitmqctl list_queues name messages consumers
curl -s http://localhost:8200/health                      # translate: {"loaded": true} после прогрева ~30с

# шаг 5 — realtime
docker compose exec centrifugo wget -qO- http://localhost:8000/api ; echo   # любой ответ = жив
curl -s http://localhost:8107/health                      # chat-сервис

# шаг 6 — RAG
curl -s http://localhost:8100/health                      # rag_api
docker compose logs rag_worker 2>&1 | tail                # загрузка embedding-модели (старт до 5 мин)

# шаг 7 — micro (если нужен): gateway пингует все сервисы
curl -s http://localhost:8000/health/detailed             # {"identity":true,...}
```

⚠️ **`core`/`mono` и `micro` не смешивать**: gateway имеет сетевой алиас
`backend` и тот же порт 8000 — два API-контейнера одновременно дают
DNS round-robin и 502. Переключение:

```bash
docker compose rm -sf backend && COMPOSE_PROFILES=micro docker compose up -d
docker compose rm -sf gateway && COMPOSE_PROFILES=mono docker compose up -d
docker compose exec -T gateway alembic upgrade head   # миграции (или backend)
```

Windows-обёртка: `start.bat [mono|micro]` — поднимает инфраструктуру,
прибивает API-контейнер чужого профиля, ждёт `/health`, гонит миграции.

### 4.2 Все health-эндпоинты

| Сервис | URL | Что проверять |
|---|---|---|
| backend (mono/core) | `http://localhost:8000/health` | liveness: `{"status":"ok","env":...}` |
| backend | `http://localhost:8000/health/detailed` | 503 только если postgres лежит; redis/rabbitmq — статус-флаги |
| gateway (micro) | `http://localhost:8000/health` и `/health/detailed` | detailed пингует identity…chat, 503 если хоть один недоступен |
| identity/catalog/billing/partners/dialog/admin/chat | `http://localhost:8101..8107/health` | `{"status":"ok","service":...}` |
| metrics | `GET http://localhost:8000/metrics` c `X-Internal-Token: <secret>` | см. §8 |
| miniapp | `http://localhost:8080/` | nginx отдаёт index.html |
| centrifugo | контейнер: `wget http://localhost:8000/api` | OSS v6 не отдаёт `/health`: любой HTTP-ответ = жив |
| translate | `http://localhost:8200/health` | `{"status":"ok","model":...,"loaded":true}` |
| rag_api | `http://localhost:8100/health` | liveness + статус брокера |
| rag_worker | внутри контейнера `http://localhost:8101/healthz` | порт `METRICS_PORT` наружу не публикуется |
| bot / worker_fetch / worker_send | heartbeat-файл (compose healthcheck) | liveness без HTTP: файл `/tmp/heartbeat_<name>` моложе 90 с |
| rabbitmq | `http://localhost:15672` | management UI; CLI: `rabbitmqctl status` |
| postgres / redis / minio / qdrant | compose healthchecks | `pg_isready` / `redis-cli ping` / TCP 9000 / TCP 6333 |

### 4.3 Пересборка и логи

```bash
docker compose build backend                 # пересобрать один сервис
docker compose up -d --build backend         # пересобрать и перезапустить
docker compose --profile micro build catalog # сервис профиля micro
docker compose up -d --build                 # всё (использует кэш pip в Dockerfile)

docker compose logs -f backend               # логи сервиса
docker compose logs --since 10m worker_fetch | grep -i error
docker compose ps                            # состояние + health
```

Образ собирается из корневого [Dockerfile](../Dockerfile)
(`python:3.13-slim`, pip-кэш-маунт, 5 попыток установки); miniapp — из
[frontend/Dockerfile](../frontend/Dockerfile) (nginx + self-signed entrypoint).

## 5. Конфиг Centrifugo (configs/centrifugo.json.tmpl)

[configs/centrifugo.json.tmpl](../configs/centrifugo.json.tmpl) —
иерархический формат v6 с плейсхолдерами. При старте контейнера `sed`
подставляет env (JSON не умеет переменные):

```
sed 's|__SECRET__|$CENTRIFUGO_SECRET|; s|__API_KEY__|$CENTRIFUGO_HTTP_API_KEY|; \
    s|__INTERNAL__|$INTERNAL_TOKEN|; s|__PROXY__|http://gateway:8000|' \
    config.tmpl > /tmp/config.json && centrifugo -c /tmp/config.json
```

Разбор блоков:

| Блок | Значение | Что делает |
|---|---|---|
| `client.token.hmac_secret_key` | `__SECRET__` | проверка браузерных connection-JWT (HS256), подписанных бэкендом тем же `CENTRIFUGO_SECRET` ([app/infrastructure/services/centrifugo.py](../app/infrastructure/services/centrifugo.py)) |
| `client.insecure` | `false` | без токена не подключиться |
| `client.allowed_origins` | `["*"]` | CORS для браузеров (в проде сузить до домена) |
| `http_api.key` | `__API_KEY__` | ключ publish-API: `POST /api` c `Authorization: apikey <key>` |
| `admin.enabled` | `false` | админ-UI Centrifugo выключен |
| `channel.proxy.subscribe.endpoint` | `__PROXY__/api/chats/subscribe-proxy` | каждая подписка на канал проходит валидацию на бэкенде (IDOR-защита каналов) |
| `channel.proxy.subscribe.http.static_headers.X-Internal-Token` | `__INTERNAL__` | статический заголовок = `INTERNAL_TOKEN` бэкенда (совпадает с `CENTRIFUGO_HTTP_API_KEY` в dev) |
| `channel.without_namespace.subscribe_proxy_enabled` | `true` | личный канал `user:{id}` тоже через proxy |
| `channel.namespaces[]` | `user`, `partner`, `thread` | каждый: `subscribe_proxy_enabled: true` + `presence: true` (онлайн-статусы через presence-API) |

Бэкенд возвращает `{}` (подписка разрешена) или
`{"error":{"code":403,...}}` — формат v6: не-2xx считается временной
ошибкой и ретраится, поэтому отказ всегда 200. Участник видит только свои
треды/каналы ([app/transport/http/routers/chats.py](../app/transport/http/routers/chats.py),
`POST /api/chats/subscribe-proxy`).

## 6. Миниапп в Telegram (валидный HTTPS)

Telegram открывает web_app только по https с доверенным сертификатом.
Self-signed из `frontend/entrypoint-ssl.sh` годится для браузера на LAN
(`https://<LAN_IP>:8443`), но Telegram его не покажет. Публичный адрес —
SSH-туннель к localhost.run:

```bat
miniapp-url.bat
```

Скрипт поднимает `ssh -R 80:localhost:8080 nokey@localhost.run`, забирает
`https://<random>.lhr.life`, пишет его в `MINIAPP_URL` (.env) и
пересоздаёт бота (web_app-кнопка ставится при старте). Проверка:
`curl -s https://<url>` — 200 без `-k`.

- URL меняется при обрыве ssh — перезапусти скрипт (старый процесс гасится сам, PID в `.miniapp_tunnel.pid`).
- trycloudflare.com блокируется провайдером (EOF), ngrok требует аккаунт.
- Постоянный адрес: аккаунт localhost.run (свой `name.lhr.life`) или именованный Cloudflare Tunnel.

Прод: Caddy + Let's Encrypt — [deploy/DEPLOY.md](../deploy/DEPLOY.md)
(`deploy/docker-compose.prod.yml`, bootstrap одной командой).

## 7. S3 / MinIO

Два bucket, общие креды `S3_*` (dev: `minioadmin/minioadmin`,
`MINIO_ROOT_USER/PASSWORD` в compose):

| Bucket | Назначение | Ключи | Политика |
|---|---|---|---|
| `raw-payloads` (`S3_BUCKET`) | JSON сырых ответов адаптеров (флаг `RAW_PAYLOAD_ENABLED`) | `raw/yyyy/mm/...` (ключ в `listings.raw_key`) | приватный; админка читает presigned GET (10 мин) |
| `market-photos` (`S3_PHOTOS_BUCKET`) | фото лотов рынка + зеркала внешних лотов | `photos/{user_id}/{uuid4}.{ext}`; зеркало `mirror/{source}/{source_id}/{NNN}.{ext}` (`index:03d` — лексикографическая сортировка = порядок фото на источнике) | public-read (создаётся идемпотентно `put_bucket_policy`) |

Поведение: bucket'ы создаются лениво (`ensure_bucket` при первом
обращении); зеркало фоток — фоновая задача `mirror_photos_task`
(первые `MARKET_PHOTO_MIRROR_LIMIT` фото на лот, батч
`MARKET_MIRROR_BATCH` лотов), её сбой не роняет fetch (warning
`mirror_task_failed`). Retention raw — cron `raw_retention_job` 03:30 UTC,
обнуляет `listings.raw_key` удалённых. Исчезнувшие внешние лоты чистятся
`stale_check_job` (04:00 UTC): зеркало + raw (owner-лоты не трогаются).

Ручные операции (dev, `mc` в контейнере):

```bash
docker compose exec minio ls /data                    # бакеты на диске
# список объектов зеркала лота:
docker run --rm --network auto_bot_default minio/mc sh -c \
  "mc alias set local http://minio:9000 minioadmin minioadmin && \
   mc ls --recursive local/market-photos/mirror/encar/"
```

## 8. Очередь RabbitMQ

Топология — [app/infrastructure/messaging/topology.py](../app/infrastructure/messaging/topology.py)
(faststream) и [app/infrastructure/messaging/legacy_worker.py](../app/infrastructure/messaging/legacy_worker.py)
(aio_pika; воркеры). Имена очередей совместимы между ними.

| Сущность | Имя | Тип/аргументы |
|---|---|---|
| exchange | `tasks` | direct, durable |
| DLX | `tasks.dlx` | fanout, durable |
| очередь `fetch` | rk `fetch_source_task`, `mirror_photos_task`, `stale_check_job`, `translate_listings`, `raw_retention_job` | quorum, `x-delivery-limit 5`, DLX |
| очередь `send` | rk `send_listing_notification`, `notify_new_listings`, `listing_status_scan`, `digest_job_*`, `subscription_check_job`, `dialog_cleanup_job`, `favorites_sold_job`, `market_placement_expiry` | quorum, `x-delivery-limit 5`, DLX |
| очередь `enrich` | rk `enrich_listing` | quorum (faststream-relay) |
| DLQ | `dead_letter` | durable, fanout-привязана к `tasks.dlx` |

Ретраи: 3 попытки (backoff 2с ×2), после — DLQ; фоновый монитор раз в
`HEALTH_CHECK_INTERVAL_SEC` алертит при глубине ≥ `DLQ_ALERT_THRESHOLD`.

Управление:

```bash
docker compose exec rabbitmq rabbitmqctl list_queues name messages consumers
docker compose exec rabbitmq rabbitmqctl purge_queue dead_letter   # чистка DLQ
# replay DLQ в exchange tasks (at-least-once, битые удаляет):
docker compose exec -T backend python -m app.infrastructure.messaging.replay --limit 100 --dry-run
docker compose exec -T backend python -m app.infrastructure.messaging.replay --limit 100
```

Management UI: `http://localhost:15672` (dev guest/guest).

Cron-расписание воркеров (UTC):

| Воркер | Задача | Расписание |
|---|---|---|
| fetch | `schedule_fetch_due` | каждые 60 с (cron источников + Redis-лок `sched:{src}:{мин}`) |
| fetch | `raw_retention_job` | 03:30 |
| fetch | `stale_check_job` | 04:00 |
| send | `market_placement_expiry` | 03:40 |
| send | `dialog_cleanup_job` | 04:00 |
| send | `favorites_sold_job` | 04:30 |
| send | `subscription_check_job` | 12:00 |
| send | `digest_job_morning` / `digest_job_evening` | 09:00 / 20:00 |

## 9. Метрики и мониторинг

- **`GET /metrics`** ([app/transport/http/routers/metrics.py](../app/transport/http/routers/metrics.py),
  защищён `X-Internal-Token`): пользователи (total/new 24h/by_tier),
  фильтры, объявления, лиды, разборы цен, уведомления за 24 ч, health
  источников (avg_latency_ms, error_streak), события 24 ч. Ответ кэшируется
  в Redis 30 с (`metrics:snapshot:v1`); БД недоступна → 503:

  ```bash
  curl -s -H "X-Internal-Token: <secret>" http://localhost:8000/metrics | jq
  ```

- **Health источников**: `GET /api/admin/sources` (internal-токен) —
  avg latency, error_streak, last_success/last_error. Circuit breaker:
  `SOURCE_CB_ERROR_STREAK` (5) ошибок подряд → источник пропускается
  до восстановления.
- **Алерты** ([app/infrastructure/services/alerting.py](../app/infrastructure/services/alerting.py)):
  Telegram на `ADMIN_CHAT_ID` (fallback `MANAGER_CHAT_ID`), дедуп
  `alert:{key}` на `ALERT_DEDUP_TTL_SEC`. Алертятся: DLQ ≥ 10,
  поломка парсера (0 карточек при ответе источника), отключение
  источника CB, CB перевода/прокси/RAG, партнёр приостановлен за неуплату,
  платежи с исчерпанными ретраями активации.
- **ParserDoctor** (`doctor_mode`): `off/suggest/auto` — при поломке
  селекторов GLM предлагает/применяет ремонт.
- **Trace**: каждый HTTP-запрос — `TraceIdMiddleware` (trace_id в логах).
- Внешняя проверка прода: `curl -fs https://DOMAIN/health` (UptimeRobot/cron).

## 10. Обновление без даунтайма

Ядро (backend/bot) не зависит от периферии (`required: false`), поэтому
периферию обновляют в любом порядке; PostgreSQL-миграции должны быть
обратимыми и совместимыми со старым кодом (additive-first).

Порядок безопасного обновления (mono-профиль, прод):

```bash
# 0) проверка миграций на копии БД
bash deploy/backup.sh
docker compose exec -T postgres psql -U app -d autoselect -c "CREATE DATABASE autoselect_check TEMPLATE autoselect"
# (на копии): upgrade head → downgrade -1 → upgrade head
docker compose exec -T postgres psql -U app -d postgres -c "DROP DATABASE autoselect_check"

# 1) новый код
git pull
# 2) пересборка и накат периферии (ядро не рестартуем)
docker compose up -d --build worker_fetch worker_send translate
docker compose up -d --build centrifugo chat        # если realtime
docker compose up -d --build qdrant rag_worker rag_api  # если ai
# 3) миграции (additive), затем ядро
docker compose exec -T backend alembic upgrade head
docker compose up -d --build backend bot
# 4) миниапп (статика; Telegram кэширует index.html — он отдаётся no-cache)
docker compose up -d --build miniapp
# 5) проверка
curl -s http://localhost:8000/health/detailed
curl -s -H "X-Internal-Token: <secret>" http://localhost:8000/metrics | jq .users
```

Даунтайм здесь — только рестарт backend (секунды; uvicorn поднимается
быстро, бот на long-polling переподключается сам). Полный zero-downtime
не заявлен: один инстанс на сервис. Откат: `git checkout <tag>` +
`docker compose up -d --build` + при необходимости `alembic downgrade -1`.

## 11. Бэкапы и восстановление

### Postgres

Ежедневный бэкап — [deploy/backup.sh](../deploy/backup.sh) (cron
`0 3 * * *`, gzip -9, ротация 14 копий в `/var/backups/autobot`):

```bash
bash deploy/backup.sh        # вручную
# эквивалент руками:
docker compose exec -T postgres pg_dump -U app -d autoselect | gzip -9 > backup.sql.gz
```

Восстановление — [deploy/restore.sh](../deploy/restore.sh)
(останавливает писателей, дропает схему, заливает дамп):

```bash
docker compose stop bot worker_fetch worker_send
bash deploy/restore.sh /var/backups/autobot/autoselect_YYYYmmdd_HHMMSS.sql.gz
docker compose up -d && docker compose exec -T backend alembic upgrade head
docker compose start bot worker_fetch worker_send
```

Тест восстановления — раз в месяц; копии офбокс раз в неделю
(scp/rclone).

### MinIO

Волюмы `minio_data` docker-снапшотом или `mc mirror`:

```bash
# бэкап обоих бакетов в локальную папку
docker run --rm --network auto_bot_default -v "$PWD/.minio-backup:/backup" minio/mc sh -c \
  "mc alias set src http://minio:9000 minioadmin <MINIO_ROOT_PASSWORD> && \
   mc mirror --overwrite src/raw-payloads /backup/raw-payloads && \
   mc mirror --overwrite src/market-photos /backup/market-photos"

# восстановление
docker run --rm --network auto_bot_default -v "$PWD/.minio-backup:/backup" minio/mc sh -c \
  "mc alias set dst http://minio:9000 minioadmin <MINIO_ROOT_PASSWORD> && \
   mc mirror --overwrite /backup/raw-payloads dst/raw-payloads && \
   mc mirror --overwrite /backup/market-photos dst/market-photos"
```

Точечный дам волюма целиком (вместе с метаданными MinIO):

```bash
docker run --rm -v auto_bot_minio_data:/data -v "$PWD":/backup alpine \
  tar czf /backup/minio_data.tgz /data
```

Важно: raw-payload невоспроизводим только для удалённых retention'ом
объектов; market-photos потеря = битые `<img>` у существующих лотов
(зеркала перезабираются повторным fetch только для активных лотов).

### Что не в бэкапах (безвозвратно)

Redis (дедуп/кэш/DialogState/FSM — восстанавливается по ходу работы),
RabbitMQ (теряются только недообработанные сообщения; DLQ при желании
дампится `rabbitmqctl export_definitions`), Qdrant (переиндексация RAG).

## 12. Прод-чеклист

1. Секреты: `APP_SECRET_KEY` ≥ 32 симв., отдельный `INTERNAL_TOKEN`,
   `CENTRIFUGO_SECRET`/`CENTRIFUGO_HTTP_API_KEY` ≠ change-me,
   `DB_PASSWORD`/`RABBITMQ_URL` без дефолтов; `APP_ENV=prod` (fail-fast).
2. Миграции на копии БД: `upgrade head` → `downgrade -1` → `upgrade head`.
3. TLS: Caddy + Let's Encrypt (`deploy/docker-compose.prod.yml`), не self-signed.
4. `ALERTS_ENABLED=true`, заполнены `ADMIN_CHAT_ID`/`MANAGER_CHAT_ID`.
5. Cron: бэкап 03:00 (`deploy/backup.sh`), тест рестора ежемесячно.
6. UFW: наружу только 22/80/443; 5432/6379/5672/9000/15672 — не публиковать.
7. `client.allowed_origins` в configs/centrifugo.json.tmpl сузить до домена.
8. Политика конфиденциальности: тексты диалогов уходят во внешний LLM
   (INC-003, [INCIDENTS.md](../INCIDENTS.md)).
9. `TRANSLATE_MAX_RSS_MB` < mem_limit (3500m), порог выше легитимного пика.
