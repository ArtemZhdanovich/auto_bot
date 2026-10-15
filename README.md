# bot-encar — Экосистема подбора автомобилей (Encar + KCar + Che168)

Telegram-бот + Mini App для подбора автомобилей из Кореи и Китая: агрегация
объявлений с нескольких источников, AI-диалоговый ассистент (GLM) с RAG-базой
знаний, уведомления по фильтрам, монетизация (Telegram Stars + CPL-лиды для
партнёров), партнёрский API и полный админ-контур.

Реализация ТЗ (`TZ.md`), этапы 0–8 + фазы 1–7 `PLAN.md`. Текущий статус и
бэклог — `PROGRESS.md` (статус-дашборд в §0).

- **Языки/рантайм**: Python 3.13 (asyncio), TypeScript/React 18
- **Менеджер пакетов**: `uv` (Python), `npm` (frontend)
- **БД/инфраструктура**: PostgreSQL 16, Redis 7, RabbitMQ 3, Qdrant, MinIO (S3)
- **Тесты**: все зелёные (`uv run pytest -q`), `ruff` clean, `mypy` настроен

---

## Содержание

1. [Архитектура](#архитектура)
2. [Стек и репозиторий](#стек-и-репозиторий)
3. [Быстрый старт](#быстрый-старт)
4. [Docker Compose: все сервисы](#docker-compose-все-сервисы)
5. [Конфигурация (.env)](#конфигурация-env)
6. [Telegram-бот](#telegram-бот)
7. [Backend API](#backend-api)
8. [Адаптеры источников и парсинг](#адаптеры-источников-и-парсинг)
9. [Очередь задач и воркеры](#очередь-задач-и-воркеры)
10. [AI-слой: LLM, агент, RAG](#ai-слой-llm-агент-rag)
11. [Микросервис RAG (services/rag)](#микросервис-rag-servicesrag)
12. [Перевод тайтлов (services/translate)](#перевод-тайтлов-servicestranslate)
13. [Сырые ответы в MinIO (raw_payload)](#сырые-ответы-в-minio-raw_payload)
14. [Монетизация: подписки и Telegram Stars](#монетизация-подписки-и-telegram-stars)
15. [Лиды и партнёрский контур с CPL-биллингом](#лиды-и-партнёрский-контур-с-cpl-биллингом)
16. [Аналитика и воронки](#аналитика-и-воронки)
17. [Мониторинг и алерты](#мониторинг-и-алерты)
18. [Безопасность](#безопасность)
19. [Модель данных и миграции](#модель-данных-и-миграции)
20. [Тесты и качество](#тесты-и-качество)
21. [Прод-чеклист](#прод-чеклист)

---

## Архитектура

```
                    ┌────────────────────────── Telegram ──────────────────────────┐
                    │  Bot API (long polling)          WebApp (Mini App :8080)     │
                    └──────────────┬───────────────────────────┬───────────────────┘
                                   │                           │ initData → JWT
                            ┌──────▼──────┐             ┌──────▼──────┐
                            │  bot        │   HTTP      │  Mini App   │ React 18 + Vite
                            │  (aiogram3) │────────────▶│  + frontend │ (nginx, /api → backend)
                            └──────┬──────┘             └──────┬──────┘
                                   │ enqueue (bot → backend)   │ REST (JWT)
                                   │                           │
┌─────────────┐             ┌──────▼───────────────────────────▼──────┐
│  Encar      │             │            backend (FastAPI :8000)      │
│  KCar       │  httpx      │  auth / users / filters / listings /    │
│  Che168 ────┼────────────▶│  favorites / payments / dialog / ai /   │
│  (адаптеры, │  (+прокси   │  notifications / leads / partners /     │
│  registry)  │  Data-      │  admin                                  │
└─────────────┘  Impulse)   └───┬──────────┬──────────┬──────────┬───────┘
                                │          │          │          │
                     ┌──────────▼───┐ ┌────▼────┐ ┌───▼───┐ ┌────▼─────────┐
                     │ PostgreSQL16 │ │ Redis 7 │ │Rabbit │ │ LLM GLM 4.6  │
                     │ (данные)     │ │ (FSM,   │ │MQ 3   │ │ api.z.ai     │
                     └──────────────┘ │ дедуп,  │ │очереди│ └────┬─────────┘
                                      │ кэши,   │ └───┬───┘      │ fallback
                                      │ лимитеры│     │          ▼
                                      └─────────┘     │    ┌──────────────┐
                                                      │    │ services/rag │ rag_api :8100
                                           ┌──────────▼───┐ │ rag_worker   │ ← Qdrant :6333
                                           │ fetch worker │ │  (RQ-ядро)   │ ← MinIO :9000
                                           │ send worker  │ └──────────────┘
                                           │ (рассылка,   │
                                           │ token bucket)│   services/translate :8200
                                           └──────────────┘   (NLLB, перевод тайтлов)
```

**Потоки данных:**

1. **Парсинг**: Fetch Worker по cron/задаче → адаптер источника → `UnifiedListing`
   → дедуп (БД + Redis) → `save_listings` (история цен, content_hash) →
   задачи `translate_listings` (опц.) и raw JSON → MinIO (опц.).
2. **Уведомления**: Fetch Worker находит совпадения с фильтрами → очередь `send`
   → Send Worker → Token Bucket → Telegram (instant/дайджест/статусы).
3. **Диалог**: юзер пишет боту / жмёт кнопку в Mini App → AI-агент (GLM) →
   tools (поиск по БД, RAG, лид) → ответ с кнопками.
4. **Монетизация**: Stars-инвойсы (Pro/Business) и CPL-лиды (партнёры).

---

## Стек и репозиторий

### Backend
| Компонент | Технология | Нюансы |
|---|---|---|
| Веб-фреймворк | FastAPI + uvicorn | `app/main.py`, OpenAPI на `/docs` |
| Бот | aiogram 3 (long polling) | FSM-состояния диалога — в Redis, не в памяти |
| ORM/миграции | SQLAlchemy 2 async + Alembic | asyncpg, декларативные `Mapped[...]` |
| Валидация | Pydantic v2 + pydantic-settings | `app/schemas/`, `Settings` в `app/core/config.py` |
| Очередь | aio-pika (RabbitMQ) | Свой мини-фреймворк `app/queue/`: TaskWorker, CronSpec, DLQ |
| Кэш/состояние | redis.asyncio | Дедуп, DialogState, rate limit, кэши LLM/перевода |
| HTTP-клиент | httpx (async) | Источники, LLM, RAG, Telegram — всё через httpx |
| S3 | aioboto3 | raw_payload в MinIO (не блокирует event loop) |
| LLM | GLM 4.6 (Zhipu/z.ai) | Fallback: Anthropic, Ollama, `mock` для тестов |
| Крипто | PQ: ML-KEM-768 + X25519 | `app/core/pq_crypto.py`, шифрование документов RAG at-rest |

### Frontend (Mini App)
- React 18 + Vite + TypeScript, UI-кит `@telegram-apps/telegram-ui`,
  тема через CSS-переменные Telegram (`--tg-theme-*`).
- Авторизация: `initData` из Telegram WebApp → `POST /api/auth/telegram` → JWT
  (sessionStorage). Вне Telegram — read-only лента.
- Dev-сервер `npm run dev` на :8080 с прокси `/api` → :8000; в compose —
  Vite build → nginx, `/api` проксируется на backend.

### Микросервисы
- **`services/rag/`** — RAG-ядро из monorepo `rag_service`: `rag_core`
  (hexagonal), `rag_worker` (:8101 health/metrics), `rag_api` (:8100),
  `kb/` (база знаний markdown), `scripts/ingest_kb.py`.
- **`services/translate/`** — NLLB-переводчик тайтлов
  (`facebook/nllb-200-distilled-600M`, CPU), :8200.

### Структура репозитория

```
app/
├── api/              # FastAPI-роутеры (см. «Backend API»)
├── bot/              # aiogram 3: handlers, button_flow, client, __main__
├── ai/               # agent, llm_client, prompts, tools, rag_client
├── adapters/         # encar, kcar, che168, registry
│   └── common/       # http, normalize, dedup, proxy (обвязка парсеров)
├── services/         # бизнес-логика (таблица ниже)
├── workers/          # fetch.py, send.py — воркеры очереди
├── queue/            # rabbit.py: enqueue_task, TaskWorker, CronSpec, DLQ
├── core/             # config, security (JWT/initData), deps*, pq_crypto
├── db/               # models.py, session.py, migrations/versions (0001–0014)
└── schemas/          # Pydantic: listing, criteria, filter, dialog, user,
                      # payment, lead, partner, rag
frontend/             # Mini App (React + Vite + TS + telegram-ui)
services/
├── gateway/          # API Gateway (профиль micro): маршрутизация /api/*
├── identity/         # микросервис: auth, users, me
├── catalog/          # микросервис: listings, favorites, filters, notifications
├── billing/          # микросервис: payments, subscription
├── partners/         # микросервис: partners (CPL), leads
├── dialog/           # микросервис: dialog, ai_search (LLM/RAG)
├── admin/            # микросервис: admin, аналитика, ассистент
├── rag/              # RAG-микросервис (ядро rag_service)
└── translate/        # NLLB-перевод тайтлов
tests/                # 435 тестов; хелперы FakeSession/FakeRedis/MockLLMClient
TZ.md                 # исходное ТЗ
PLAN.md               # план фаз 1–7 (перевод, S3, партиционирование, …)
PROGRESS.md           # статус-дашборд: что готово / что ждёт владельца
docker-compose.yml    # вся инфраструктура (dev/staging)
.env.example          # зеркало Settings + интерполяция compose
```

### Сервисы `app/services/` (бизнес-логика)

| Модуль | Назначение |
|---|---|
| `users.py` | регистрация/онбординг, лимиты тарифа, tier-логика |
| `search.py` | выборка объявлений по `SearchCriteria` (лента, AI-поиск) |
| `notify.py` | instant/дайджест-уведомления, антидубли, тихие часы |
| `subscription.py` | тарифы, лимиты, активация Pro/Business по Stars |
| `fetch.py` | сохранение объявлений: дедуп, история цен, content_hash, S3 |
| `storage.py` | `S3Storage` (aioboto3): upload/retention/presign, fallback |
| `partners.py` | ключи API, квоты, claim лидов, CPL-биллинг, suspended/reactivate |
| `rate_limit.py` | Token Bucket Telegram (Redis+Lua): 30 msg/s, 1 msg/s/чат |
| `analytics.py` | события воронки в `analytics_events` (не ломает поток) |
| `funnels.py` | SQL-дашборды: конверсии, retention D1/D7 |
| `alerting.py` | алерты в Telegram админу с дедупом `alert:{key}` |
| `source_health.py` | health источников: latency, error_streak, circuit breaker |
| `parser_doctor.py` | GLM-ремонт сломавшихся CSS-селекторов (doctor_mode) |
| `translate_client.py` | клиент translate-сервиса: кэш, retry, circuit breaker |
| `dialog.py` | DialogState в Redis: TTL 24ч, история 8 сообщений |
| `admin_assistant.py` | AI-ассистент админки |
| `admin_audit.py` | журнал админ-действий в `admin_actions` |
| `cron.py` | регистрация CronSpec-задач воркеров |

---

## Быстрый старт

### MVP одной командой (сайт + бот + LLM)

```bash
bash scripts/mvp.sh up          # postgres/redis/rabbitmq + миграции + backend/bot/miniapp
bash scripts/mvp.sh up --parse  # + fetch-воркер и 1 страница encar (данные в ленте)
bash scripts/mvp.sh down        # остановить приложение (инфраструктура остаётся)
```

Скрипт проверяет `.env` (BOT_TOKEN, APP_SECRET_KEY, GLM_API_KEY; без ключа
LLM добавьте `LLM_PROVIDER=mock`) и печатает адреса после старта.

Сделать себя супер-админом демо (алерты админу, `role=admin`, тариф
business на 30 дней — без лимитов фильтров и AI):

```bash
# 1. напишите боту /start в Telegram
bash scripts/mvp.sh whoami      # 2. найдите свой telegram_id в списке
bash scripts/mvp.sh admin <id>  # 3. назначить супер-админа (бот перезапустится)
```

### Локально (по процессам)

```bash
# 1. Зависимости
uv sync

# 2. Конфиг
cp .env.example .env   # минимум: BOT_TOKEN, APP_SECRET_KEY

# 3. Инфраструктура
docker compose up -d postgres redis rabbitmq

# 4. Миграции
uv run alembic upgrade head

# 5. Backend
uv run uvicorn app.main:app --reload          # :8000

# 6. Бот (другой терминал)
uv run python -m app.bot

# 7. Воркеры (ещё два терминала)
uv run python -m app.workers.fetch
uv run python -m app.workers.send

# 8. Mini App dev-сервер (ещё один)
cd frontend && npm install && npm run dev     # :8080
```

Проверка: `http://localhost:8000/health`, Swagger: `http://localhost:8000/docs`,
Mini App: `http://localhost:8080` (вне Telegram — read-only лента).

### Всё сразу через compose (профили)

```bash
cp .env.example .env

docker compose --profile mono up --build    # монолит (backend :8000)
docker compose --profile micro up --build   # микросервисы (gateway :8010)
```

Профиль `micro` поднимает gateway и 6 доменных сервисов; gateway получает
сетевой алиас `backend`, поэтому бот и Mini App работают без правок конфига
(см. раздел «Микросервисы» ниже). Без флага поднимается только
инфраструктура.

### Микросервисы RAG/перевод (опционально, в обоих профилях)

```bash
# RAG (нужны GLM_API_KEY, RAG_API_TOKEN в .env)
docker compose up -d qdrant minio rag_worker rag_api
uv run --project services/rag python scripts/ingest_kb.py --force

# Перевод тайтлов
docker compose up -d translate   # + TRANSLATE_ENABLED=true в .env
```

---

## Docker Compose: все сервисы

| Сервис | Образ | Порты | Назначение / нюансы |
|---|---|---|---|
| `postgres` | postgres:16 | 5432 | БД `autoselect` (юзер `app`), healthcheck `pg_isready`. Пароль `DB_PASSWORD` |
| `redis` | redis:7-alpine | 6379 | Дедуп, FSM, кэши, лимитеры, digest-буферы (DB 0); RAG использует DB 1 |
| `rabbitmq` | rabbitmq:3-management | 5672, **15672 UI** | Очереди `fetch`/`send` + DLQ; guest/guest по умолчанию |
| `backend` *(mono)* | build . | **8000** | Монолит: все роутеры одного FastAPI; healthcheck `/health` |
| `bot` *(mono+micro)* | build . | — | `python -m app.bot`; внутри сети ходит в `http://backend:8000` |
| `worker_fetch` *(mono+micro)* | build . | — | Парсинг, кроны 03:30/04:00 UTC |
| `worker_send` *(mono+micro)* | build . | — | Рассылка; Token Bucket на Redis |
| `miniapp` *(mono+micro)* | frontend/Dockerfile | **8080** | Vite build → nginx, `/api` → backend |
| `gateway` *(micro)* | build . | **8010** | Единая точка входа `/api/*`; сетевой алиас `backend` |
| `identity` *(micro)* | build . | 8101 | auth / users / me |
| `catalog` *(micro)* | build . | 8102 | listings / favorites / filters / notifications |
| `billing` *(micro)* | build . | 8103 | payments / subscription |
| `partners` *(micro)* | build . | 8104 | partner API / admin партнёров / leads |
| `dialog` *(micro)* | build . | 8105 | dialog / ai (LLM, RAG) |
| `admin` *(micro)* | build . | 8106 | админ-контур, аналитика, ассистент |
| `qdrant` | qdrant/qdrant | 6333 | Векторный индекс RAG |
| `minio` | minio/minio | 9000, 9001 (консоль) | raw_payload (S3 API); minioadmin/minioadmin — **сменить в проде** |
| `rag_migrate` | postgres:16 | — | one-shot: создаёт БД `rag` в общем postgres |
| `rag_migrate_alembic` | build services/rag | — | one-shot: `alembic upgrade head` для БД rag |
| `rag_worker` | build services/rag | 8101 (внутр.) | Консьюмеры очередей RAG, healthz :8101 |
| `rag_api` | build services/rag | **8100** | HTTP-шлюз `POST /api/v1/ask` (Bearer `RAG_API_TOKEN`) |
| `translate` | build services/translate | **8200** | NLLB-перевод, `GET /health`; optional — падение не критично |

Все сервисы с `env_file: .env` — S3/перевод/RAG-переменные пробрасываются
автоматически. Внутри docker-сети имена хостов: `postgres`, `redis`,
`rabbitmq`, `minio`, `qdrant`, `backend` (например, `S3_ENDPOINT_URL=http://minio:9000`).

---

## Микросервисы (профиль micro)

Монолит (`app/main.py`, профиль `mono`) и микросервисы используют **одни и
те же роутеры** (`app/api/*`) и общий shared kernel (`app/core`, `app/db`,
`app/queue`, `app/schemas`) — различается только точка входа. Это даёт
выбор режима флагом compose без ветвления кода.

| Сервис | Домен | Роутеры |
|---|---|---|
| `identity` | авторизация и профили | auth, users, me |
| `catalog` | объявления и подборки | listings, favorites, filters, notifications |
| `billing` | монетизация Stars | payments, subscription |
| `partners` | CPL-контур и лиды | partners (+admin), leads |
| `dialog` | AI-диалог | dialog, ai_search |
| `admin` | админ-контур | admin (аналитика, ассистент, источники) |
| `gateway` | маршрутизация | `/api/*` → сервис по таблице `services/gateway/routing.py` |

**Ключевые решения:**

- **Ноль правок у потребителей.** Gateway слушает :8000 внутри сети и имеет
  алиас `backend` — бот (`APP_BASE_URL=http://backend:8000`) и nginx Mini App
  (`proxy_pass http://backend:8000`) не меняются.
- **Аутентификация на границе домена.** Gateway только маршрутизирует
  (метод/path/query/заголовки/тело); `X-Internal-Token`, `Authorization` и
  `X-API-Key` проходят сквозь него, проверяют их доменные сервисы.
- **Специфичные маршруты раньше общих**: `/api/users/{id}/subscription` →
  billing (до правила `/api/users` → identity), `/api/admin/partners` →
  partners (до `/api/admin` → admin). Таблица покрыта юнит-тестами.
- **Отказ домена ≠ отказ платформы**: недоступный сервис → gateway отвечает
  502 с именем сервиса; `GET /health/detailed` агрегирует пинги всех шести.

**Локально (без Docker), по процессам:**

```bash
uv run uvicorn services.gateway.main:app --port 8010   # gateway
uv run uvicorn services.identity.main:app --port 8101  # и т.д. 8102–8106
```

**Тесты сервисов** (`tests/`): `test_gateway_routing.py` — юнит-таблицы
маршрутов (специфичность, `{param}`, неизвестные пути);
`test_gateway_proxy.py` — прокси на httpx.MockTransport (метод/тело/заголовки/
query, 404 мимо таблицы, 502 упавшего апстрима, агрегированный health);
`test_services_smoke.py` — смоук каждого сервиса: `/health`, границы домена
(ровно свои префиксы, без чужих), по одному представительному эндпоинту
через реальные dependency-overrides.

---

## Конфигурация (.env)

Полное зеркало `app/core/config.py` — см. `.env.example` (там же комментарии
по каждому блоку). Ключевые группы и **нюансы**:

### Обязательный минимум
- `BOT_TOKEN` — токен бота (@BotFather). ⚠️ Старый токен отозван — перед
  продом перевыпустить.
- `APP_SECRET_KEY` — подпись JWT **и** (по умолчанию) X-Internal-Token.
- `DATABASE_URL`, `REDIS_URL`, `RABBITMQ_URL`.

### Авторизация
- `INTERNAL_TOKEN` — отдельный секрет для админ-эндпоинтов; пусто = используется
  `APP_SECRET_KEY` (обратная совместимость).
- `ACCESS_TOKEN_TTL_SEC=3600`, `REFRESH_TOKEN_TTL_SEC=2592000` (30 дней),
  `INIT_DATA_MAX_AGE_SEC=86400` — initData старше суток отклоняется.

### LLM
- `LLM_PROVIDER`: `glm` | `anthropic` | `ollama` | `mock` (тесты/dev без LLM).
- `GLM_API_KEY`, `GLM_BASE_URL` (по умолчанию международный
  `https://api.z.ai/api/paas/v4`; для ключей open.bigmodel.cn —
  `https://open.bigmodel.cn/api/paas/v4`).
- `LLM_TEMPERATURE=0.2`; модели `LLM_DEFAULT_MODEL`/`LLM_ADVANCED_MODEL`.

### RAG
- `RAG_SERVICE_URL` — **пусто = RAG выключен** (агент отвечает через LLM).
- `RAG_ENABLED=true` — feature flag отката; `RAG_API_TOKEN` — Bearer шлюза.
- `RAG_TIMEOUT_SEC=5`, `RAG_CACHE_TTL_SEC=3600` (Redis),
  `RAG_CONFIDENCE_THRESHOLD=0.6` (ниже — fallback на LLM),
  circuit breaker: 5 ошибок подряд → пауза 300 с.
- `RAG_JWT_SECRET` — общий секрет rag_api↔rag_worker (пусто = `APP_SECRET_KEY`).

### Уведомления
- `NOTIFY_RATE_LIMIT_SEC=30` — не чаще 1 сообщения/30 с на юзера (п. 8.6).
- `TELEGRAM_RATE_GLOBAL_PER_SEC=30` / `TELEGRAM_RATE_CHAT_PER_SEC=1` —
  Token Bucket (0 = выкл; Redis лежит → fail-open).
- `NOTIFY_SENT_TTL_SEC` = 7 дней антидубля `notified:{uid}:{lid}`.
- `NOTIFY_QUIET_HOURS_DEFAULT` — глобальные тихие часы `"23:00-08:00"`.
- `STALE_MAX_AGE_DAYS=3` — не виден N дней → `is_active=false`.

### Перевод (optional)
- `TRANSLATE_ENABLED=false` по умолчанию; упал/выключен → показывается
  оригинальный тайтл (fallback обязателен по дизайну).
- `TRANSLATE_SOURCE_LANGS` — JSON-карта источник→NLLB-код
  (`encar/kcar` → `kor_Hang`, `che168` → `zho_Hans`); прочие — auto-детект.
- Кэш переводов в Redis 30 дней; CB: 3 ошибки → пауза 5 мин + алерт.

### S3/MinIO (optional)
- `RAW_PAYLOAD_ENABLED=false` по умолчанию; пустой `S3_ENDPOINT_URL` = выкл.
- `S3_BUCKET=raw-payloads`, `RAW_RETENTION_DAYS=180`,
  `RAW_PRESIGN_TTL_SEC=600`.
- Ошибка S3 **не ломает** сохранение объявления: `raw_key = NULL`.

### Прокси DataImpulse (optional)
- Заданы `PROXY_DATAIMPULSE_USER`+`PASS` → источники с
  `capabilities.use_proxy=true` (che168) ходят через шлюз, остальные напрямую.
- `__cr.<geo>` — гео выхода (`cn` для che168); `STICKY_TTL_SEC=1800` —
  sticky-сессия IP, `0` = ротация на каждый запрос.
- CB: 5 ошибок соединения подряд → пауза 5 мин (ходим напрямую).

### Партнёры и биллинг
- `PARTNER_RATE_LIMIT_PER_MIN=60` — fixed window на ключ в Redis.
- `PARTNER_CPL_RATES={"starter": 5.0, "pro": 3.0, "salon": 2.0}` — CPL-тарифы
  по планам (USD/лид); индивидуальный `cpl_rate` партнёра перекрывает план.

### Мониторинг
- `ALERTS_ENABLED=true` → алерты в Telegram на `ADMIN_CHAT_ID`;
  `ALERT_DEDUP_TTL_SEC=1800` — подавление повторов;
  `DLQ_ALERT_THRESHOLD=10` — глубина DLQ для алерта;
  `SENTRY_DSN`, `LOG_LEVEL`.

### PQ-шифрование (optional)
- `ENCRYPT_DOCUMENTS` + `PQ_ENCRYPTION_PUBLIC_KEY`/`PRIVATE_KEY`
  (ML-KEM-768 + X25519). Генерация:
  `uv run python -c "from app.core.pq_crypto import generate_keypair; print(generate_keypair())"`.

---

## Telegram-бот

`app/bot/` — aiogram 3, long polling (`python -m app.bot`). FSM-состояния —
в Redis (переживают рестарт), энкрипция не нужна: ТЗ не требует.

**Команды**: `/start` (онбординг + deep-link), `/menu`, `/help`, `/reset`
(сброс AI-диалога), `/stop` (пауза уведомлений), `/resume`,
`/subscribe` (тарифы).

**Колбэки**:
- `menu:*` — главное меню (поиск, фильтры → Mini App, подписка, справка);
- `sub:buy:<tier>` — инвойс Stars на тариф;
- `dlg:*` — кнопочные ответы AI-диалога (fallback-режим без LLM).

**Deep-link**: `/start <payload>` обрабатывается (`handle_deep_link`) —
например, открытие карточки/фильтра из Mini App.

**AI-диалог** (`_agent_turn`): свободный текст → AI-агент (см. ниже);
backend/LLM недоступен → переключение на кнопочный режим (fallback, п. 7.9).

**Платежи Stars**: `pre_checkout_query` валидируется, `successful_payment`
активирует тариф (идемпотентно по charge id).

---

## Backend API

Аутентификация — три схемы (зависимости в `app/core/deps*.py`):

| Схема | Заголовок | Кто | Что защищает |
|---|---|---|---|
| JWT (Telegram initData или web-логин) | `Authorization: Bearer` | юзеры Mini App | users/me/filters/listings/favorites/payments |
| Internal Token | `X-Internal-Token` | админка/крон/бот | `/api/admin/*`, `/metrics`, `/api/dialog/*`, `/api/notifications/*`, `/api/leads/*` |
| Partner API Key | `X-API-Key` | партнёры (CPL) | `/api/partner/*` |

### Пользователи и профили
- `POST /api/auth/telegram` — валидация initData (HMAC, TTL 24 ч) → JWT.
- `POST /api/auth/register`, `POST /api/auth/login` — веб-регистрация/вход
  (миграция 0003); `POST /api/auth/refresh` — обмен refresh-токена на новую
  пару (access 1 ч / refresh 30 дней). `POST /api/auth/magic-link` — заглушка
  (501, этапы 3–4 ТЗ).
- `GET /api/me` — профиль; `POST /api/me/onboarded` — флаг онбординга
  (UI/UX ТЗ §2.8); `POST /api/users/upsert` (internal) — создание/обновление
  пользователя ботом.

### Фильтры и лента
- `GET/POST/PATCH/DELETE /api/filters[/{id}]` — CRUD, лимит 20 на юзера
  (Free 3 / Pro 20 / Business ∞), criteria валидируется `SearchCriteria`.
- `GET /api/listings` — лента с фильтрами make/model/year/price/mileage/source,
  пагинация `limit/offset` (≤50), новые сверху.
- `GET /api/listings/{id}` — карточка + история цен (`listing_price_history`).
- `GET/POST/DELETE /api/favorites[...]` — избранное; отдаёт `first_price`
  (бейдж «⬇️ цена снизилась») и `is_active=false` (бейдж «🔴 исчезло»).

### Диалог и AI-поиск
- `POST /api/dialog/message` (internal) — ход диалога: юзер → агент → ответ.
- `POST /api/ai/message` (JWT) — тот же агент из Mini App (AI-подбор авто);
  `POST /api/ai/reset`, `POST /api/ai/finalize` — сброс/финализация критериев.

### Уведомления
- `POST /api/notifications/settings` (internal) — instant/digest, тихие часы
  (вызывает бот: /stop //resume и меню «Уведомления»).
- `POST /api/notifications/scan` (internal) — принудительное сканирование.

### Лиды (воронка диалог → обращение)
- `POST /api/leads` (internal) — создать лид (бот/агент после квалификации);
  событие `lead_created`.
- `PATCH /api/leads/{id}` — статус менеджера: new/in_progress/closed/rejected.

### Монетизация (Stars)
- `GET /api/subscription` — статус, лимиты, цены.
- `POST /api/payments/stars` — инвойс (XTR) + `createInvoiceLink`.
- `GET /api/payments/history` — история платежей.

### Админка (`/api/admin`, X-Internal-Token)
- `GET /api/admin/adapters` — зарегистрированные адаптеры.
- `GET /api/admin/sources`, `PUT /api/admin/sources/{name}` — конфиг источника
  в БД (base_url, page_size, proxy и т.д. — без деплоя).
- `GET /api/admin/sources/{name}/health` — latency/ошибки/CB.
- `POST /api/admin/sources/{name}/fetch?max_pages=N` — ручной запуск парсинга.
- `GET /api/admin/leads` — список лидов со статусами.
- `POST /api/admin/assistant/chat` — AI-ассистент админки.
- `GET /api/admin/listings/{id}/raw` — presigned GET сырого JSON из MinIO
  (10 мин; `raw_key IS NULL` → 404, S3 недоступен → 502).
- `GET /api/admin/analytics/funnel?days=30` — воронка
  search→dialog→lead→subscription (конверсии к предыдущему этапу и к первому).
- `GET /api/admin/analytics/retention?days=30` — когорты по дню регистрации,
  D1/D7 (абсолютные и %).

### Партнёры — админ (`/api/admin/partners`)
- `POST /api/admin/partners` — создать партнёра + выдать ключ (сырой ключ
  показывается **один раз**, хранится только SHA-256).
- `GET /api/admin/partners` — список со статистикой (claimed_today, баланс).
- `POST .../{id}/rotate` — ротация ключа (старый мгновенно инвалиден);
  `POST .../{id}/revoke` — отзыв всех ключей.
- `PATCH .../{id}` — план/квота/статус/cpl_rate (`reset_cpl_rate` → тариф плана).
- `POST .../{id}/billing` — пополнение баланса (>0, снимает suspended) или
  корректировка (<0); пишется в `partner_transactions`.

### Партнёры — API (`/api/partner`, X-API-Key)
- `GET /api/partner/me` — аккаунт: квота, баланс, цена следующего лида.
- `GET /api/partner/leads` — пул доступных лидов (ПД скрыты до claim);
  `?mine=1` — свои лиды (с ПД).
- `POST /api/partner/leads/batch` — «взять N» (квота **и** баланс).
- `POST /api/partner/leads/{id}/claim` — эксклюзивный claim (409 taken,
  429 квота, **402 нет денег**).
- `POST /api/partner/leads/{id}/feedback` — качество лида:
  claimed/accepted/returned/junk/deal.
- `GET /api/partner/billing` — план, баланс, каталог тарифов.

### Системные
- `GET /health` — liveness.
- `GET /health/detailed` — postgres/redis/rabbitmq; 503 если лежит БД.
- `GET /metrics` (internal) — юзеры/новые 24ч, тарифы, объявления, отправленные
  уведомления, health источников, события воронки за 24 ч.

---

## Адаптеры источников и парсинг

`app/adapters/` — каждый источник реализует контракт `BaseAdapter`
(ТЗ п. 6): `capabilities()`, `fetch(criteria, page)` → список `UnifiedListing`.

| Адаптер | Источник | Нюансы |
|---|---|---|
| `EncarAdapter` | encar.com (HTML) | CSS-селекторы в конфиге БД → чинятся без деплоя (и ParserDoctor'ом) |
| `KCarApiAdapter` | kcar.com Search API | `{totalCnt, list[]}`; цены в манах × `price_unit` (10 000 → KRW); пагинация с 1-й страницы |
| `Che168Adapter` | che168.com (HTML, Китай) | Цены в 万 (10k CNY) → нормализация; гео-блокировки → прокси DataImpulse `GEO=cn`; `enabled=true` в БД после сверки с живым HTML |

**Общая обвязка (`adapters/common/`)**:
- `http.py` — httpx-клиент с таймаутами, ретраями, UA;
- `normalize.py` — нормализация марок/моделей/топлива/КПП → единый словарь;
- `dedup.py` — 2 уровня: БД (уникальный `(source, source_id)`, content_hash) +
  Redis (VIN/фuzzy между источниками);
- `proxy.py` — `GatewayProxyPool` (DataImpulse): sticky-сессии, CB при ошибках.

**Registry** (`adapters/registry.py`): `ADAPTER_CLASSES` → инстансы по
enabled-источникам из таблицы `sources` (name, priority, capabilities,
config JSON). Источник глушится/включается в БД, приоритет — порядок в ленте.

**Пайплайн сохранения** (`services/fetch.py: save_listings`):
1. Дедуп внутри источника (`source, source_id`).
2. NEW или **изменившийся content_hash** → UPDATE (снимок в историю цен
   `listing_price_history`; PK `(id, recorded_at)` — подготовка к
   партиционированию, миграция 0013).
3. Смена хэша/новое → задача `translate_listings` (если включен перевод)
   и upload raw JSON в S3 (если включен).
4. Совпадения с фильтрами → `enqueue_task` в очередь `send`.

---

## Очередь задач и воркеры

`app/queue/rabbit.py` — мини-фреймворк поверх aio-pika:

- `enqueue_task(queue, name, payload)` — JSON-сообщение; очередь = routing key.
- `TaskWorker` — консьюмер с retry, фатальные ошибки → **DLQ**
  (dead letter); мониторинг DLQ каждые 300 с, глубина ≥ 10 → алерт.
- `CronSpec(fn, hour, minute)` — cron внутри воркера.

**Fetch Worker** (`python -m app.workers.fetch`), очередь `fetch`:
задачи `fetch_source` (парсинг источника), `translate_listings`;
cron: `raw_retention_job` 03:30 UTC (retention MinIO), `stale_check_job`
04:00 UTC (is_active=false для объявлений, которых нет ≥ 3 дней;
CB-источники пропускаются).

**Send Worker** (`python -m app.workers.send`), очередь `send`:
задачи `send_listing_notification`, `send_listing_status`, `notify_new_listings`,
`listing_status_scan`; cron: `digest_job` 09:00 и 20:00 UTC,
`subscription_check_job` 12:00 UTC.

**Перед отправкой в Telegram** (`send_telegram_message`):
1. **Token Bucket** (Redis+Lua): ~30 msg/s глобально, ~1 msg/s на чат;
   нет слота → sleep до 3 попыток, потом отправка всё равно; Redis лежит →
   fail-open.
2. 429 от Telegram → `retry_after` → sleep → 1 повтор.
3. Персональный rate limit 30 с/юзера + антидубль 7 дней — до постановки в
   очередь (в `notify.py`).

---

## AI-слой: LLM, агент, RAG

### LLM-клиент (`app/ai/llm_client.py`)
Провайдеры: `glm` (основной, api.z.ai), `anthropic`, `ollama`, `mock`.
OpenAI-совместимый протокол; `LLM_BASE_URL` для прокси/Ollama.
Ошибки > 5 подряд → переключение на резерв, дальше — кнопочный режим.

### Агент (`app/ai/agent.py`)
Диалог подбора авто: извлекает критерии (марка/бюджет/год/…) в
`SearchCriteria`, зовёт tools, отвечает с кнопками. История — 8 последних
сообщений, DialogState в Redis с TTL 24 ч. FAQ-ответы кэшируются (24 ч).

### Tools (`app/ai/tools.py`)
- поиск по БД по критериям (через `services/search.py`);
- `answer_question` — вопрос в RAG (ниже);
- создание лида (после AI-квалификации: бюджет/срок/готовность к предоплате —
  `Lead.qualification` поднимает цену лида).

### RAG-клиент (`app/ai/rag_client.py`)
`POST /api/v1/ask` (Bearer): retry только на сетевые ошибки, circuit breaker
(5 ошибок подряд → пауза 5 мин), таймаут 5 с, кэш ответов в Redis
(`rag:answer:<sha256>`, TTL 1 ч). Confidence ≥ 0.6 → ответ базы знаний;
ниже → ответ LLM; и его нет → «рекомендую уточнить у менеджера».
Промпт-защита: содержимое kb — справочные данные, не инструкции
(анти-инъекции через документы); цифры без источника называть нельзя.

---

## Микросервис RAG (services/rag)

Локальный RAG на ядре [rag_service](https://github.com/Pavel14701/rag_service)
(monorepo-версия): гибридный поиск (vector + BM25/RRF), агентный retrieval,
multi-provider LLM, DLQ/retry через RabbitMQ.

- `rag_core/` — ядро (domain / application / infrastructure, hexagonal);
- `rag_worker/` — консьюмеры `query_queue`, `ingest_queue`, `delete_queue`,
  `reindex_queue`; health/metrics на :8101;
- `rag_api/` — шлюз `POST /api/v1/ask` (internal token → JWT → RPC в
  `query_queue`), :8100;
- `kb/` — база знаний (markdown: FAQ, таможня, доставка, риски, расходы);
- `scripts/ingest_kb.py` — заливка kb в MinIO/Postgres/очередь индексации.

Инфраструктура: `qdrant`, `minio`, `rag_migrate*`, `rag_worker`, `rag_api`
(см. таблицу compose). БД `rag` — в общем PostgreSQL. Опциональное
постквантовое шифрование документов at-rest: `ENCRYPT_DOCUMENTS=true` +
PQ-ключи (ML-KEM-768 + X25519).

Запуск и заливка базы знаний:
```bash
docker compose up -d qdrant minio rag_worker rag_api
docker compose exec rag_worker alembic upgrade head   # если one-shot не отработал
uv run --project services/rag python scripts/ingest_kb.py --force
```

---

## Перевод тайтлов (services/translate)

Optional-микросервис NLLB (`facebook/nllb-200-distilled-600M`, CPU, :8200):
переводит `title` объявлений (корейский/китайский → русский).
**Некритичен**: упал/выключен → UI показывает оригинал.

- API: `POST /translate {texts, source_lang, target_lang}` → `{translations}`
  (батчи; `null` = не переведено; `source_lang="auto"` — детект по письменности);
  `GET /health`; Bearer `API_TOKEN` опционально.
- Триггер — тот же content_hash: NEW/изменение → `translate_listings` →
  `title_raw/title_ru/source_language/translated_at` (миграция 0011).
- Клиент `translate_client.py`: Redis-кэш `tr:{tgt}:{src}:{sha1}` (30 дней),
  retry, circuit breaker (3 ошибки → пауза 5 мин + алерт), батчи по 32,
  таймаут 30 с (NLLB на CPU медленный).

Включение: `TRANSLATE_ENABLED=true` + `docker compose up -d translate`.

---

## Сырые ответы в MinIO (raw_payload)

Сырые JSON от encar/kcar/che168 — «холодные» данные (аудит, отладка парсера):
в PostgreSQL **не хранятся** (колонка `raw_payload` выпилена миграцией 0012,
вместо неё `listings.raw_key` — ключ S3-объекта). Загрузка через **aioboto3**.

- Ключ: `raw/{source}/{yyyy}/{mm}/{listing_id}_{content_hash}.json` —
  retention по префиксу + дедупликация (хэш не менялся → не перезаписывается).
  S3 Metadata: source, listing_id, content_hash, fetched_at.
- Загружаются только NEW/изменившиеся; ошибка S3 → объявление сохранено,
  `raw_key = NULL` (fallback обязателен по дизайну).
- Retention: cron `raw_retention_job` (03:30 UTC) удаляет объекты старше
  `RAW_RETENTION_DAYS` (180) и обнуляет `raw_key` (чанками по 1000).
- `ensure_bucket` при старте воркера: токен без права CreateBucket → warning,
  не падаем (bucket создаётся заранее).
- Админка: `GET /api/admin/listings/{id}/raw` → presigned GET на 10 минут;
  прямого доступа к MinIO у UI нет.

Включение: `RAW_PAYLOAD_ENABLED=true`, `S3_ENDPOINT_URL` (внутри сети —
`http://minio:9000`), `docker compose up -d minio`.

---

## Монетизация: подписки и Telegram Stars

Тарифы (`app/services/subscription.py`, ТЗ п. 10.2):

| Тариф | Цена | Фильтры | AI/день | Instant |
|---|---|---|---|---|
| Free | 0 | 3 | 20 | нет |
| Pro | 500★/мес, 4800★/год (−20%) | 20 | 200 | да |
| Business | 2500★/мес, 24000★/год | ∞ | ∞ | да |

- `POST /api/payments/stars` — pending-платёж + `createInvoiceLink` (валюта
  XTR); бот выдаёт инвойс, `pre_checkout_query` валидируется,
  `successful_payment` активирует тариф идемпотентно.
- Cron `subscription_check_job` (12:00 UTC) — напоминания об истечении.
- Живой тестовый платёж XTR пока не проводился (код-путь готов).

---

## Лиды и партнёрский контур с CPL-биллингом

**Лиды** (ТЗ, P1): юзер в диалоге/Mini App оставляет обращение →
`POST /api/leads` → менеджер ведёт статусы (`PATCH /api/leads/{id}`:
new/in_progress/closed/rejected). AI-квалификация (бюджет/срок/предоплата)
сохраняется в `Lead.qualification` — квалифицированные лиды попадают в
партнёрский пул.

**Партнёрский контур** (P2-11/14): ИП/перекупы/салоны покупают лиды (CPL).

- Доступ по `X-API-Key` (`pi_…`, хранится только SHA-256; ротация мгновенно
  инвалидает старый ключ). Rate limit 60 req/min на ключ (fixed window Redis).
- Планы: `starter` / `pro` / `salon` — дневная квота лидов и CPL-тариф
  (5 / 3 / 2 USD; `PARTNER_CPL_RATES`).
- **Claim** — эксклюзивный: `partner_id` ставится атомарно, ПД (user_id)
  отдаются только после claim. Batch-claim учитывает квоту **и** баланс
  (`affordable = balance // cost`).
- **Биллинг** (миграция 0014):
  - при claim списывается цена лида; транзакция `charge_lead` пишется в
    `partner_transactions` с `balance_after` (аудит-трейл);
  - баланс < цены → claim отклонён (**402**), партнёр автоматически
    `suspended` + алерт админу (дедуп 24 ч);
  - пополнение `POST /api/admin/partners/{id}/billing` (amount > 0) снимает
    suspended и пишет транзакцию `topup`; отрицательный amount — ручная
    корректировка (`adjustment`), suspended не снимает;
  - индивидуальный `cpl_rate` партнёра (PATCH админки) перекрывает тариф
    плана; `reset_cpl_rate` возвращает план.
- **Фидбек** партнёра по качеству лида (`accepted/deal` vs `junk/returned`);
  события `partner_lead_claimed`, `partner_lead_feedback` — в `analytics_events`.

---

## Аналитика и воронки

`analytics_events` (миграция 0004) — только факты воронки, без ПД в props:
`user_registered`, `search_completed`, `dialog_message`, `lead_created`,
`partner_lead_claimed`, `partner_lead_feedback`, `subscription_activated`.
`track()` никогда не ломает бизнес-поток (ошибка → warning).

Дашборды (P2-13, `services/funnels.py`, read-only SQL):
- `GET /api/admin/analytics/funnel?days=N` — distinct-юзеры на каждом этапе
  search→dialog→lead→subscription, конверсии к предыдущему этапу
  (`conv_prev_pct`) и к первому (`conv_first_pct`);
- `GET /api/admin/analytics/retention?days=N` — когорты по дню
  `user_registered`: размер когорты, D1/D7 абсолютные и в % (D7 «молодой»
  когорты = 0, пока неделя не прошла).

Быстрые агрегаты за 24 ч — в `GET /metrics` (`events_24h`).

---

## Мониторинг и алерты

- **Health**: `/health` (liveness), `/health/detailed` (postgres/redis/rabbitmq;
  503 только если лежит БД — остальные компоненты деградация, не смерть).
- **Метрики**: `/metrics` (X-Internal-Token) — юзеры/фильтры/объявления/
  уведомления 24 ч, разбивка по тарифам, health источников, события 24 ч.
- **Health источников** (`source_health`): avg latency, error_streak;
  circuit breaker: 5 ошибок подряд → источник временно отключается
  (`SOURCE_CB_ERROR_STREAK`).
- **Алерты** (`services/alerting.py`): Telegram на `ADMIN_CHAT_ID` с дедупом
  `alert:{key}` (TTL 30 мин по умолчанию). Алертятся: DLQ ≥ 10, поломка
  парсера, отключение источника CB, открытие CB перевода/прокси/RAG,
  партнёр приостановлен за неуплату.
- **ParserDoctor** (`doctor_mode` в конфиге источника): при поломке
  селекторов GLM предлагает ремонт; `off` / `suggest` / `auto`.
- `SENTRY_DSN` — опционально.

---

## Безопасность

- **JWT**: HS256; для Mini App — подпись `initData` валидируется по HMAC
  (`APP_SECRET_KEY`, TTL initData 24 ч); для веба — логин/refresh (30 дней).
- **X-Internal-Token** — отдельный секрет `INTERNAL_TOKEN` (пусто =
  `APP_SECRET_KEY`): все админ-эндпоинты, `/metrics`, `/api/dialog/*`.
  Админ-действия журналируются в `admin_actions` (payload без секретов).
- **Партнёрские ключи**: только SHA-256 в БД; сырой ключ показывается один
  раз при создании/ротации; префикс — для опознания в ЛК.
- **ПД**: `user_id` лида отдаётся партнёру только после эксклюзивного claim;
  пул лидов — без ПД.
- **PQ-шифрование** (optional): ML-KEM-768 + X25519 для документов RAG
  at-rest (`ENCRYPT_DOCUMENTS`).
- **Минимум привилегий S3**: токен без права CreateBucket обрабатывается
  мягко (warning, без падения); presigned GET — на 10 минут.

---

## Модель данных и миграции

Основные таблицы (`app/db/models.py`): users, user_filters, sources,
source_health, listings, listing_price_history, favorites, sent_log,
analytics_events, leads (+partner-поля, qualification), partner_accounts,
partner_api_keys, partner_transactions, payments, admin_actions, web_auth.

| Миграция | Что делает |
|---|---|
| 0001 initial | ядро: users, sources, listings, filters, sent_log, … |
| 0002 favorites | избранное |
| 0003 web_auth | веб-авторизация (JWT по логину) |
| 0004 analytics_events | события воронки |
| 0005 add_che168_source | источник che168 |
| 0006 partner_accounts | партнёры + API-ключи + partner-поля лидов |
| 0007 onboarded_at | флаг онбординга |
| 0008 admin_actions | аудит админ-действий |
| 0009 listing_tracking | отслеживание объявлений |
| 0010 sent_log_kind | тип уведомления в sent_log |
| 0011 translations | title_raw/title_ru/source_language/translated_at |
| 0012 raw_payload_s3 | DROP raw_payload → listings.raw_key (S3-ключ) |
| 0013 partition_ready | PK (id, recorded_at), NOT NULL — подготовка к партиционированию по времени (pg_partman; план в PLAN §6) |
| 0014 partner_billing | balance, cpl_rate + partner_transactions (CPL-журнал) |

Накат: `uv run alembic upgrade head`. ⚠️ Перед продом прогнать на копии БД:
`upgrade head` → `downgrade -1` → `upgrade head`.

---

## Тесты и качество

```bash
uv run pytest -q                 # все зелёные
uv run ruff check app tests
uv run mypy app                  # настроен
cd frontend && npm run build     # tsc --noEmit + vite build
```

Покрытие: LLM-клиенты, RAG-клиент (fallback, CB, кэш), агент, уведомления,
адаптеры (контракты, дедуп, che168), auth, PQ-крипто, мониторинг/алерты,
cron/планировщик, аналитика/воронки, лиды, ParserDoctor, прокси-шлюз,
партнёрский контур + биллинг, S3 storage, token bucket.

Тест-хелперы (`tests/conftest.py`): `FakeSession` (строковые PK, rollback,
`get`, `executed_params`), `FakeRedis` (`ttls`), `MockLLMClient`
(`script`, `.calls`).

---

## Прод-чеклист

Единственное, что отделяет от «зелёного» статуса — действия владельца
(детали: `PROGRESS.md` §24 и §32):

1. **Перевыпустить `BOT_TOKEN`** (@BotFather — старый отозван) → `.env`.
2. `alembic upgrade head` — сначала на **копии БД** (upgrade → downgrade -1 → upgrade).
3. Секреты: `APP_SECRET_KEY`, `INTERNAL_TOKEN`, `GLM_API_KEY`,
   `RAG_API_TOKEN`, `RAG_JWT_SECRET`; сменить minioadmin/minioadmin и
   guest/guest RabbitMQ.
4. Живые проверки: Stars XTR-платёж; che168 (сверка HTML + `GEO=cn` →
   `enabled=true`); RAG (`ingest_kb.py --force` + контрольный запрос);
   MinIO e2e (fetch → presigned → retention); биллинг (партнёр → topup →
   claim → списание).
5. Опционально: `doctor_mode=suggest`, `TRANSLATE_ENABLED`,
   `RAW_PAYLOAD_ENABLED`, `ENCRYPT_DOCUMENTS`.
6. `APP_ENV=prod`, публичный HTTPS `MINIAPP_URL` (домен/TLS).

Дальше по бэклогу (P3): LLM cost tracking, ротация PQ-ключей, push ветки
`refactor/monorepo`, рефакторинг веб-авторизации (nullable telegram_id).







