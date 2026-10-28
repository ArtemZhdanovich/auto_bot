# Архитектура auto_bot

> Документ — карта системы для нового инженера: по этому файлу (плюс
> [ADR.md](ADR.md)) должно быть понятно, из чего состоит проект, как
> крутится каждый поток данных и где лежит соответствующий код.
> Все факты сверены с кодом на 2026-10-28 (волна NAV-V2).

## 0. Сводка

**Продукт**: Telegram-бот + Mini App + партнёрский кабинет для подбора
автомобилей из Кореи (агрегация encar.com / kcar.com / che168.com),
внутренний рынок объявлений пользователей и партнёров, чаты
(поддержка/продавец/сделка/переговорные), AI-контур (анализ цены,
отчёты истории, генерация описаний, RAG-бот), монетизация через
Telegram Stars и CPL-лиды партнёров.

**Стек** (см. `pyproject.toml`, `frontend/package.json`):

| Слой | Технологии |
|---|---|
| Бэкенд | Python 3.13 (asyncio), FastAPI, SQLAlchemy 2 async (asyncpg), Pydantic v2, dishka (DI), aio-pika + FastStream (RabbitMQ), aiogram 3 (бот) |
| Фронтенд | React 18 + TypeScript, @telegram-apps/telegram-ui, react-router-dom (BrowserRouter), vite, vitest |
| Данные | PostgreSQL 16 (alembic, 30 миграций), Redis 7, RabbitMQ 3, MinIO (S3), Qdrant (RAG), Centrifugo v6 (websocket) |
| Инфра | docker-compose со ступенчатыми профилями; прод — Caddy (deploy/) |

**Точки входа процесса**:

| Команда | Файл | Что делает |
|---|---|---|
| `uvicorn app.main:app` | `app/main.py` | совместимая обёртка → `app.main_api` |
| `uvicorn app.main_api:app` | `app/main_api.py` | монолитный HTTP API (профиль mono/core) |
| `python -m app.transport.bot` | `app/transport/bot/__main__.py` | aiogram-бот (long polling, FSM в Redis) |
| `python -m app.infrastructure.workers.fetch` | `app/infrastructure/workers/fetch.py` | воркер очереди `fetch` (парсинг, перевод, зеркало) |
| `python -m app.infrastructure.workers.send` | `app/infrastructure/workers/send.py` | воркер очереди `send` (уведомления, дайджесты, cron) |
| `python -m app.main_worker` | `app/main_worker.py` | relay outbox → RabbitMQ (at-least-once) |
| `python -m app.main_consumer` | `app/main_consumer.py` | консьюмер очереди `enrich` (FastStream + dishka) |
| `uvicorn services.gateway.main:app` | `services/gateway/main.py` | API-gateway (профиль micro) |
| `uvicorn services.<name>.main:app` | `services/*/main.py` | 7 доменных сервисов (профиль micro) |
| `rag-api` / `rag-worker` | `services/rag/` | RAG-микросервис (свой alembic, Qdrant) |
| `translate-api` | `services/translate/translate_api/main.py` | NLLB-перевод тайтлов |

## 1. Общая карта системы

```text
                         ┌────────────────────────────────────────────────┐
                         │                     Клиенты                    │
   Telegram (бот, web_app)                     │                      Браузер
   ┌──────────────┐   miniapp-url.bat          ▼                           │
   │  aiogram bot │──────────────┐    ┌──────────────────┐                 │
   │ (long-poll,  │              │    │  Mini App (SPA)  │  /partner  /admin
   │  FSM в Redis)│              │    │ frontend/src     │  — отдельные
   └──────┬───────┘              │    └───┬──────────┬───┘  поверхности
          │ BackendClient        │        │ REST /api│ ws (centrifuge-js)
          │ http://backend:8000  │        ▼          ▼
          │              (nginx миниаппа: /api → backend:8000)
          ▼                      │        │
   ┌─────────────────────────────────────────────┐        ┌──────────────────┐
   │  HTTP API                                   │        │  Centrifugo v6   │
   │  mono:  app/main_api.py  (один процесс)     │        │  (профиль        │
   │  micro: services/gateway → 7 доменных       │◄──────►│  realtime)       │
   │          сервисов services/* (общий код app/)│ publish│ namespaces:     │
   │  роутеры: app/transport/http/routers/*      │ +sub   │ user/partner/   │
   └───┬──────────┬──────────┬──────────┬────────┘ proxy  │ thread          │
       │          │          │          │                  └──────────────────┘
       ▼          ▼          ▼          ▼
 ┌─────────┐ ┌─────────┐ ┌────────┐ ┌──────────────────────────┐
 │Postgres │ │ Redis 7 │ │RabbitMQ│ │ MinIO (S3)              │
 │ autoselect│ │ кэш/   │ │queues: │ │ bucket raw-payloads:    │
 │ 30 мигр. │ │ rate/  │ │ fetch  │ │  raw/{source}/{yyyy}/{mm}│
 │ + БД rag │ │ FSM/   │ │ send   │ │ bucket market-photos:   │
 │ (qdrant- │ │ локи   │ │ enrich │ │  photos/{uid}/{uuid}.ext│
 │  контур) │ │         │ │ +DLQ   │ │  mirror/{src}/{id}/NNN. │
 └─────────┘ └─────────┘ └───┬────┘ └──────────────────────────┘
       ▲          ▲          │                ▲
       │          │          ▼                │
       │          │   ┌────────────────────────────────────┐
       │          │   │ воркеры (отдельные процессы)       │
       │          │   │ worker_fetch: парсинг encar/kcar/  │
       │          │   │   che168 → upsert listings →       │
       │          │   │   enqueue notify/translate/mirror  │
       │          │   │ worker_send: TG-уведомления,       │
       │          │   │   дайджесты, экспирация размещений │
       │          │   │ main_worker: outbox relay          │
       │          │   │ main_consumer: enrich              │
       │          │   └────────────────────────────────────┘
       │          │
       │   ┌────────────────┐   ┌───────────────┐   ┌──────────────────┐
       └──►│ services/rag   │   │services/      │   │ внешние API:     │
           │ rag_api :8100  │   │translate      │   │ Encar/Kcar/Che168│
           │ rag_worker     │   │:8200 (NLLB)   │   │ GLM (z.ai) /     │
           │ Qdrant + БД rag│   └───────────────┘   │ Anthropic,       │
           └────────────────┘                       │ DataImpulse proxy│
                                                    └──────────────────┘
```

Потоки данных (кратко):

1. **Инжест**: cron-планировщик fetch-воркера (каждую минуту, Redis-лок
   `sched:{source}:{мин}`) → очередь `fetch` → адаптер источника →
   upsert `listings` (дедуп по `content_hash`) → постановка
   `notify_new_listings` / `translate_listings` / `mirror_photos_task`.
2. **Чтение**: браузер/TG → nginx миниаппа (`/api` → `backend:8000`) →
   монолит или gateway → доменный сервис → Postgres (+кэш Redis).
3. **Realtime**: REST-операция (сообщение/typing/read) →
   `ParticipantEventPublisher` → publish в личные каналы `user:{tg_id}` /
   `partner:{id}` всех участников → Centrifugo → фронт (`chatStore`);
   фоллбэк — поллинг REST каждые 5 с.
4. **Фон**: cron-задачи send-воркера (дайджесты 09:00/20:00 UTC,
   подписки 12:00, чистки 04:00–04:30, экспирация размещений 03:40) →
   Telegram Bot API с token-bucket-лимитами.

## 2. Монолит `app/` — Clean Architecture

### 2.1 Слои и правила зависимостей

```text
┌────────────────────────────────────────────────────────────┐
│ transport/        FastAPI-роутеры (thin), aiogram-бот,      │
│                   хендлеры очередей (transport/messaging)   │
├────────────────────────────────────────────────────────────┤
│ application/      use cases (сценарии) + DTO + порты        │
│                   (Protocol) + AppError                     │
├────────────────────────────────────────────────────────────┤
│ domain/           чистые правила: тарифы, квоты, говернанс, │
│                   глоссарий, критерии поиска (без I/O)      │
├────────────────────────────────────────────────────────────┤
│ infrastructure/   SQLAlchemy-репозитории, RabbitMQ, Redis,  │
│                   S3, Centrifugo, LLM, адаптеры источников  │
└────────────────────────────────────────────────────────────┘
        зависимости строго сверху вниз; domain не импортирует
        ничего; склейка слоёв — только app/di (dishka)
```

Правила (видны по импортам в коде):

- `app/domain/**` — без SQLAlchemy/FastAPI, только stdlib + типы
  (`app/domain/subscription/policies.py` — тарифы,
  `app/domain/pricing/rules.py` — квоты анализов,
  `app/domain/translation/glossary.py` — глоссарий hangul→латиница,
  `app/domain/search/criteria.py` — `SearchCriteria`,
  `app/domain/shared/errors.py`, `app/domain/shared/contacts.py` —
  маскировка контактов).
- `app/application/**` — импортирует домен и объявляет **порты**
  (`Protocol`-классы в `ports.py` каждого пакета: `chat/ports.py`,
  `catalog/ports.py`, `market/ports.py`, `identity/ports.py`,
  `partners/ports.py`, `billing/ports.py`, `dialog/ports.py`,
  `shared/ports.py`). Реализации инфраструктуры сюда не попадают.
- Ошибки: домен бросает свои исключения, use case переводит их в
  `AppError` из `app/application/errors.py` (`NotFound`, `Forbidden`,
  …; единственный язык ошибок для transport, `code` + `public_message`).
- DTO — inline-модули `dto.py` в каждом пакете application
  (`MessageView`, `ThreadOut`, `PartnerLeadView`, …), Pydantic-схемы
  HTTP-слоя отдельно в `app/transport/http/schemas/`.

### 2.2 Пакеты application (по доменам)

`admin`, `ai_search`, `alerts`, `analysis` (AI-разбор цены),
`analytics`, `audit`, `auth`, `billing`, `calc`, `catalog` (лента,
фасеты, карточка), `chat` (треды/сообщения/говернанс/бот поддержки),
`dialog` (AI-диалог подбора), `favorites`, `filters`, `identity`
(токены/гости/веб-вход), `leads`, `listings`, `market` (лоты рынка,
модерация, качество, фото, чеки партнёров), `metrics`,
`notifications`, `parser_doctor` (GLM-ремонт селекторов), `partners`,
`payments`, `pricing`, `profile`, `quota`, `search`, `shared`,
`subscription`, `translation`, `users`.

Полный список сценариев собирается автоматической проводкой dishka
(`provide_all`) в `app/di/providers/use_cases.py` — от `AuthenticateTelegram`
и `CreateGuestSession` до `SendMessage`, `CreateMarketLot`, `ModerateLot`,
`BuildHistoryReport`, `ConfirmPayment`. Читается как реестр возможностей.

### 2.3 Transport

- `app/transport/http/routers/` — 20 роутеров: `auth`, `users`, `me`,
  `metrics`, `filters`, `listings`, `analysis` (+`analysis.bot_router` —
  внутренний канал бота, `X-Internal-Token`), `deals`, `calc`,
  `favorites`, `payments`, `admin`, `chats` (+`chats.admin_router`),
  `dialog`, `ai_search`, `notifications`, `leads`, `partners`
  (+`partners.admin_router`), `market` (`market_router`, `partner_router`,
  `admin_router` — очередь модерации). Роутеры тонкие: `route_class=
  DishkaRoute`, use case приходит через `FromDishka[...]`, валидация —
  Pydantic-схемы.
- `app/transport/http/deps/` — аутентификация как dishka-провайдеры
  (`HttpDepsProvider`: `CurrentUser` из Bearer JWT, `PartnerAuthState`
  из `X-API-Key`); внутренние эндпоинты — `InternalDishkaRoute`
  (проверка `X-Internal-Token` до выполнения хендлера).
- `app/transport/http/middleware/trace.py` — `TraceIdMiddleware` +
  trace-логирование.
- `app/transport/bot/` — aiogram: `handlers.py` (основные сценарии),
  `button_flow.py` (инлайн-кнопки), `client.py` (`BackendClient` к
  HTTP API, таймаут 120 c под LLM-разборы), `throttle.py`
  (`FloodMiddleware` — скользящее окно на пользователя), FSM в Redis
  (`fsm:{user_id}`), глобальный error-хендлер.
- `app/transport/messaging/handlers/` — консьюмеры FastStream (очередь
  `enrich`), зависимости — `FromDishka` (сессия REQUEST-скоуп).

### 2.4 Infrastructure

| Модуль | Назначение |
|---|---|
| `infrastructure/db/` | `models.py` (все ORM-таблицы), `mappers.py`, `session.py` (engine/пул), `uow.py` (`SqlAlchemyUnitOfWork`), `outbox.py` (`OutboxDAO`), `repositories/` (SQLAlchemy-реализации портов), `migrations/` (alembic) |
| `infrastructure/messaging/` | `topology.py` (exchanges/очереди для FastStream), `legacy_worker.py` (aio_pika publisher/`TaskWorker`: dispatch, retry, cron, DLQ-монитор), `publisher.py` (outbox-буфер), `relay.py` (outbox relay), `idempotency.py` (`RedisIdempotencyGuard`), `replay.py` (CLI повторов DLQ), `telegram.py` |
| `infrastructure/cache/` | Redis-кэш (facets, translate, history-report, chat-summary) |
| `infrastructure/security/` | `JwtTokenIssuer`/`JwtAccessTokenVerifier`, `TelegramInitDataVerifier`, `Pbkdf2PasswordHasher`, `RedisLoginCodeStore`, `ip_limit.py` (гостевой лимит по IP) |
| `infrastructure/services/` | `centrifugo.py` (`CentrifugoPublisher`: publish/presence/JWT), `alerting.py` (алерты админу с дедупом), `fetch.py` (`save_listings`, `mark_stale`), `source_health.py` (circuit breaker источников), `parser_doctor.py`, `translate_client.py`, `storage.py` (raw S3) |
| `infrastructure/sources/` | адаптеры источников: `encar.py`, `kcar.py`, `che168.py` (SSR-парсер), `registry.py` (`AdapterRegistry` — классы из кода, конфиг из таблицы `sources`, креды — `credentials_ref` → env), `common/` (http, proxy-пул DataImpulse, dedup, normalize, translate) |
| `infrastructure/storage/` | `s3.py` (raw-payloads), `photos.py` (market-photos: загрузки + зеркало) |
| `infrastructure/ai/` | LLM-клиент (glm/anthropic/ollama/mock), RAG-клиент с circuit breaker |
| `infrastructure/translation/` | NLLB-обвязка |
| `infrastructure/workers/` | `fetch.py`, `send.py` (см. §6) |
| `infrastructure/runtime/` | heartbeat-файлы для healthcheck консьюмеров без HTTP-порта |

## 3. DI: dishka-контейнеры и скоупы

Сборка — `app/di/container.py::make_container(*extra)`; порядок
провайдеров = порядок слоёв, `extra` регистрируется последним и
перекрывает зависимости (так в тестах подменяют БД/Redis фейками).

| Провайдер (`app/di/providers/`) | Скоуп | Что даёт |
|---|---|---|
| `SettingsProvider` | APP | `Settings` (singleton `get_settings`, `app/config.py`) |
| `DatabaseProvider` | APP / REQUEST | APP: `AsyncEngine` (pool_pre_ping, statement_timeout 30 s, lock_timeout 5 s, idle_in_transaction 60 s), `async_sessionmaker`; REQUEST: `AsyncSession`, `SqlAlchemyUnitOfWork`, `OutboxDAO` |
| `RedisProvider` | APP | `redis.asyncio.Redis` (socket_timeout 5 s) |
| `BrokerProvider` | APP / REQUEST | APP: `RabbitBroker` (FastStream, graceful_timeout 30 s), `RawEventPublisher`; REQUEST: `BufferedEventPublisher` (flush после commit) |
| `RepositoryProvider` | REQUEST | SQLAlchemy-репозитории портов (`repositories.py`, `provide_all`) |
| `AdapterProvider` | REQUEST (часть APP) | инфраструктурные адаптеры портов: RAG-клиент, translate, S3, Centrifugo, лимитеры, LLM, источники |
| `SecurityProvider` | APP | `TokenIssuer`, `AccessTokenVerifier`, `InitDataVerifier`, `PasswordHasher`, `LoginCodeStore` |
| `UseCaseProvider` | REQUEST | все сценарии (`provide_all` + явные фабрики: `TriggerScan`, `MessageRateLimiter`, `ParticipantEventPublisher`, `SendMessage`, `IssueWsToken`, `GetListingRawPayload`) |
| `HttpDepsProvider` (`app/transport/http/deps/providers.py`) | REQUEST | `CurrentUser` / `PartnerAuthState`; Starlette `Request` берётся из dishka-контекста (`FastapiProvider.from_context`) |

Ключевые решения:

- **FastAPI `Depends` не используется вовсе** — ни для use case, ни для
  аутентификации; всё через `setup_dishka` + `DishkaRoute` (см.
  `app/main_api.py`).
- Скоупы: **APP** — долгоживущее (engine, redis, broker, настройки),
  **REQUEST** — на HTTP-запрос/сообщение очереди (сессия БД, UoW,
  use case, буфер событий).
- Один и тот же контейнер используется монолитом
  (`app/main_api.py`) и каждым доменным сервисом (`services/_base.py::
  create_domain_service` вызывает `make_container()`).

## 4. Микросервисы `services/*`

Общий принцип (`services/_base.py`): сервис — тонкая FastAPI-обёртка
(`/health` + роутеры монолита из `app/transport/http/routers/`), тот же
dishka-контейнер. Один код живёт в mono (все роутеры одним процессом)
и в micro (по процессу на домен) — дублирования нет.

| Сервис | Порт | Роутеры | Назначение |
|---|---|---|---|
| `gateway` | 8000 (алиас `backend`) | — | единая точка `/api/*`, httpx-прокси (таймаут `GATEWAY_TIMEOUT_SEC=150` — LLM-разборы до 120 s), `/health/detailed` пингует все сервисы |
| `identity` | 8101 | `auth`, `users`, `me` | аутентификация (initData/JWT/гости/веб-вход), профиль |
| `catalog` | 8102 | `listings`, `analysis`(+bot), `deals`, `favorites`, `filters`, `notifications`, `calc` | лента, PDP, AI-разбор цены, фильтры, калькулятор |
| `billing` | 8103 | `payments` | Stars-платежи, подписки, продления размещений |
| `partners` | 8104 | `partners.admin_router`, `partners.router`, `leads` | партнёрский кабинет (API-key), CPL-биллинг, лиды |
| `dialog` | 8105 | `dialog`, `ai_search` | AI-диалог подбора, AI-поиск (профиль ai) |
| `admin` | 8106 | `admin`, `chats.admin_router`, `market.admin_router`, `metrics` | админка, модерация, операторы поддержки, метрики |
| `chat` | 8107 | `chats`, `market.market_router`, `market.partner_router` | чаты + внутренний рынок (профиль realtime; публикует в Centrifugo) |
| `rag` | api 8100 | `POST /api/v1/ask`, `GET /health` | RAG-база знаний: собственная БД `rag` (alembic `services/rag/migrations`), воркер `rag_worker` (RabbitMQ + Qdrant + MinIO), PQ-крипта опционально |
| `translate` | 8200 | `POST /translate`, `GET /health` | NLLB (`facebook/nllb-200-distilled-600M`) на CPU, глоссарий на стороне клиента |

### 4.1 Маршрутизация gateway (`services/gateway/routing.py`)

Правила «первый совпавший префикс побеждает», `{param}` = один сегмент.
Порядок критичен (специфичные раньше общих):

| Префикс | Сервис |
|---|---|
| `/api/users/{user_id}/subscription` | billing |
| `/api/users`, `/api/auth`, `/api/me` | identity |
| `/api/partner/offers`, `/api/partner/chats`, `/api/partner/market`, `/api/partner/listings` | chat |
| `/api/chats`, `/api/market` | chat |
| `/api/listings`, `/api/favorites`, `/api/filters`, `/api/notifications`, `/api/calc`, `/api/bot` | catalog |
| `/api/payments`, `/api/subscription` | billing |
| `/api/partner`, `/api/admin/partners`, `/api/leads` | partners |
| `/api/dialog`, `/api/ai` | dialog |
| `/api/admin` | admin |

Заголовки `Authorization` / `X-Internal-Token` / `X-API-Key` проходят
сквозь gateway без изменений — аутентификация остаётся в доменных
сервисах. В compose контейнер gateway получает сетевой алиас `backend`,
поэтому бот (`APP_BASE_URL=http://backend:8000`) и nginx миниаппа
работают в micro без правок; общий хост-порт 8000 с монолитом делает
профили mono/micro взаимоисключающими ( bind-конфликт вместо тихих 502).

### 4.2 Когда можно жить без микропрофиля

Профиль `micro` — последняя ступень compose; до него продукт полностью
работает в `mono` (те же роутеры одним процессом). RAG и translate —
опциональные сервисы и в mono: `RAG_SERVICE_URL` пуст/выключен →
`answer_question` отвечает чистым LLM; `TRANSLATE_ENABLED=false` →
тайтлы показываются в оригинале (fallback обязателен, `app/config.py`).

## 5. База данных (PostgreSQL `autoselect`)

ORM: `app/infrastructure/db/models.py`; миграции: alembic,
`app/infrastructure/db/migrations/versions/` (alembic.ini в корне).
Схема RAG-сервиса — отдельная БД `rag` (свой alembic в `services/rag/`).

### 5.1 Реестр миграций (0001–0030)

| # | Файл | Суть | Ключевые таблицы/колонки |
|---|---|---|---|
| 0001 | `0001_initial.py` | начальная схема (ТЗ 5.8) | users, filters, sources, source_health, sent_log, dialog_sessions, payments, listings, leads |
| 0002 | `0002_favorites.py` | избранное | favorites (PK user_id+listing_id) |
| 0003 | `0003_web_auth.py` | веб-вход с сайта | users.email (unique), users.password_hash |
| 0004 | `0004_analytics_events.py` | продукт-воронка | analytics_events |
| 0005 | `0005_add_che168_source.py` | источник Китай (SSR-парсер) | строка sources: che168 |
| 0006 | `0006_partner_accounts.py` | партнёрский контур CPL | partner_accounts, partner_api_keys; leads.partner_id/partner_status/claimed_at/qualification |
| 0007 | `0007_onboarded_at.py` | онбординг | users.onboarded_at |
| 0008 | `0008_admin_actions.py` | аудит админов | admin_actions |
| 0009 | `0009_listing_tracking.py` | отслеживание объявлений | listings.content_hash/first_seen_at/last_seen_at/disappeared_at/price_changed_at; listing_price_history |
| 0010 | `0010_sent_log_kind.py` | тип уведомления | PK sent_log → (user_id, listing_id, kind: new/price_drop/gone/returned) |
| 0011 | `0011_translations.py` | перевод тайтлов | listings.title_raw/title_ru/source_language/translated_at |
| 0012 | `0012_raw_payload_s3.py` | сырые payload → S3 | listings.raw_key (вместо JSONB); ключ `raw/{source}/{yyyy}/{mm}/{id}_{hash}.json` |
| 0013 | `0013_price_history_partition_ready.py` | подготовка к партиционированию | PK listing_price_history → (id, recorded_at NOT NULL) |
| 0014 | `0014_partner_billing.py` | биллинг партнёров | partner_accounts.balance/cpl_rate; partner_transactions |
| 0015 | `0015_price_analysis.py` | AI-разбор цены | price_analyses (кэш + квоты) |
| 0016 | `0016_listing_specs_dialog_ttl.py` | спеки лотов | listings.body_type/fuel/transmission/drive; dialog_sessions.updated_at |
| 0017 | `0017_engine_volume_color.py` | обогащение с detail-API | listings.engine_volume (литры, CHECK 0–20), listings.color |
| 0018 | `0018_inspection_reports.py` | чеки осмотра | inspection_reports (marks/items/grade, unique listing_id) |
| 0019 | `0019_outbox.py` | транзакционные события | outbox |
| 0020 | `0020_specs_flag_partner_tx_unique.py` | техдолг 3.6/3.7/2.4 | listings.specs_enriched_at; uq_partner_tx_lead_kind; dialog_sessions.id default UUID |
| 0021 | `0021_users_source_and_email_lower.py` | воронка | users.source + idx; функциональный unique `lower(email)` |
| 0022 | `0022_inspection_images.py` | фото чека | inspection_reports.images |
| 0023 | `0023_listing_options.py` | комплектация | listings.options (JSONB) |
| 0024 | `0024_partner_owner_market.py` | владелец партнёрского счёта | partner_accounts.owner_telegram_id (unique) → авто-Business |
| 0025 | `0025_leads_idempotency.py` | корректирующая (схема-дрейф) | leads.idempotency_key + partial unique |
| 0026 | `0026_chat_market.py` | чаты + витрина | chat_threads/messages/participants, partner_offers |
| 0027 | `0027_market_owners.py` | рынок с владельцами | listings.owner_type/owner_user_id/owner_partner_id/placement_*; payments.kind/listing_id; partner_accounts.placement_slots(_used) |
| 0028 | `0028_group_history.py` | переговорные + история | chat_threads: type+='group', title; listings.owner_history (JSONB ≤50 событий) |
| 0029 | `0029_group_governance.py` | говернанс | chat_participants.role/muted; chat_threads.closed_at; chat_events; chat_reports |
| 0030 | `0030_listing_lifecycle.py` | жизненный цикл лотов | listings.status/rejected_reason/moderation_payload/pending_patch |

### 5.2 Главные таблицы (схема-выжимка)

**`listings`** (`app/infrastructure/db/models.py::Listing`) — единая
таблица и для внешних лотов (encar/kcar/che168, `owner_type IS NULL`),
и для owner-лотов рынка:

- идентификация: `source`+`source_id` (UNIQUE), `url`, `vin`;
- контент: make/model/year/price/currency/mileage_km/body_type/fuel/
  transmission/drive/engine_volume/color, `images` JSONB, `options`
  JSONB, `title_raw`/`title_ru`/`source_language`/`translated_at`;
- инжест: `content_hash` (дедуп), `parsed_at`, `first_seen_at`,
  `last_seen_at`, `disappeared_at`, `price_changed_at`, `raw_key`,
  `schema_version`, `is_active`, `specs_enriched_at`;
- рынок: `owner_type` ('user'|'partner', два частичных CHECK — ровно
  один owner_id заполнен), `owner_description`, `placement_free_until`,
  `placement_paid_until`, `owner_history`;
- lifecycle (миграция 0030): `status` CHECK IN
  ('draft','moderation','accepted','rejected','archived','expired')
  с server_default 'accepted' (внешние лоты — мимо модерации),
  `rejected_reason`, `moderation_payload` ({violations, risk_score}),
  `pending_patch` (версия-на-модерации при правке опубликованного);
- индексы: make_model, year, price, vin, last_seen, частичные
  `idx_listings_active`, `idx_listings_active_source_parsed` (лента),
  engine_volume, owner, status, частичные `idx_listings_moderation_queue`
  (status='moderation') и `idx_listings_pending_patch`.

**`users`**: PK `telegram_id` (BigInteger), role CHECK
('buyer','dealer','salon','admin'), `subscription_tier` CHECK
('free','pro','business') + `subscription_until` (NULL = бессрочный
Business по партнёрству), email/password_hash (веб-вход), `onboarded_at`,
`source` (воронка: miniapp/bot/web/telegram_link).

**`payments`**: `provider` CHECK ('stars','yookassa','crypto'),
`kind` CHECK ('subscription','placement') + `listing_id` (продление
размещения), UNIQUE (`provider`,`provider_payment_id`) — идемпотентность
вебхуков.

**Чаты**: `chat_threads` (PK UUID = имя канала `thread:{uuid}`, type
CHECK ('support','seller','deal','group'), частичные unique: support —
один на юзера, seller — один на пару (юзер, лот), deal — один на лид;
`bot_paused`, `closed_at`); `chat_messages` (sender_type CHECK
('user','operator','partner','bot','system'), индекс
`(thread_id, id DESC)` под пагинацию); `chat_participants` (PK
thread+user_type+user_id, `last_read_message_id` — unread считается
сообщениями позже маркера, role/muted); `chat_events` (аудит
created/joined/left/invited/kicked/archived/reported/muted/unmuted);
`chat_reports` (жалобы, частичный unique открытой жалобы).

**Партнёры**: `partner_accounts` (plan CHECK
('starter','pro','salon'), status, balance Numeric(12,2), `cpl_rate`
(перекрывает план), `owner_telegram_id` unique, `placement_slots`/
`placement_slots_used`); `partner_transactions` (kind CHECK
('topup','charge_lead','adjustment'), частичный unique
(`lead_id`,`kind`) — защита от двойного списания); `partner_api_keys`
(`key_hash` unique, status active/rotated/revoked).

**`leads`**: status CHECK ('new','in_progress','closed','rejected'),
partner_status ('claimed','accepted','returned','junk','deal'),
`idempotency_key` (partial unique — ретраи бота).

Прочее: `filters` (criteria JSONB, notify_mode instant/digest/off,
quiet_hours), `sources`+`source_health` (конфиг адаптеров и CB),
`sent_log` (антидубль уведомлений, PK user+listing+kind),
`dialog_sessions` (UUID, criteria/messages JSONB, TTL-чистка),
`price_analyses`, `inspection_reports`, `analytics_events`,
`admin_actions`, `partner_offers` (витрина: listing_id UNIQUE),
`outbox`.

### 5.3 Где смотрится дрейф схемы

История миграции 0025 (`leads.idempotency_key`): колонка жила в ORM,
но не в миграции → на живой БД падал POST. Выводы, зашитые в процесс:
колонка добавляется только вместе с миграцией; прод-чеклист
(docs/OPERATIONS.md) требует прогон `upgrade head → downgrade -1 →
upgrade head` на копии БД. ORM-модель (`models.py`) — единственный
источник правды о фактической схеме; alembic-файлы — история её
изменений.

## 6. Очереди RabbitMQ

Топология (`app/infrastructure/messaging/topology.py` + `legacy_worker.py`):

| Сущность | Тип | Назначение |
|---|---|---|
| exchange `tasks` | direct, durable | все задачи; routing key = имя job |
| очередь `fetch` | quorum, `x-delivery-limit=5`, DLX | парсинг/фон (binding: `fetch_source_task`, `mirror_photos_task`, `stale_check_job`, `translate_listings`, `raw_retention_job`) |
| очередь `send` | quorum, DLX | уведомления (`send_listing_notification`, `send_listing_status`, `notify_new_listings`, `listing_status_scan`, `digest_job*`, `subscription_check_job`, `favorites_sold_job`, `market_placement_expiry`) |
| очередь `enrich` | quorum, DLX | FastStream-консьюмер `app/main_consumer.py` (доменные события через outbox) |
| exchange `tasks.dlx` | fanout | dead-letter |
| очередь `dead_letter` | durable | сообщения после исчерпания попыток; глубина ≥ `DLQ_ALERT_THRESHOLD`(10) → алерт админу; replay-CLI `messaging/replay.py` |

Формат сообщения: JSON `{"id", "job", "args", "kwargs"}` (семантика
унаследована от arq). Ретраи — двухуровневые:

- in-process: `MAX_ATTEMPTS=3` с backoff 2 s × 2^n (`TaskWorker._process`);
- на уровне брокера: quorum `x-delivery-limit=5` → DLX → `dead_letter`.

`job_timeout` (asyncio.wait_for): **fetch = 360 s** (encar: чеки +
перевод NLLB; 120 s не хватало), **send = 60 s**. Параллелизм:
fetch 3, send 5 (semaphore + prefetch). Невалидный JSON — ack сразу
(иначе зациклится), неизвестный job — ack с логом.

Cron/interval (`legacy_worker.py`, защита от дублей между воркерами —
Redis-лок `cron:{job}:{мин}` / `interval:{job}:{slot}`):

| Расписание (UTC) | Задача | Воркер |
|---|---|---|
| каждую минуту | `schedule_fetch_due` — sources.schedule_cron → очередь fetch | fetch |
| 03:30 | `raw_retention_job` — raw-объекты старше N дней + raw_key=NULL | fetch |
| 03:40 | `market_placement_expiry` — экспирация размещений рынка | send |
| 04:00 | `stale_check_job` (is_active=false + S3-чистка) / `dialog_cleanup_job` | fetch / send |
| 04:30 | `favorites_sold_job` — «продано из избранного» | send |
| 09:00 / 20:00 | дайджесты утро/вечер | send |
| 12:00 | `subscription_check_job` — истёкшие подписки → free | send |

**Outbox** (миграция 0019): доменные события пишутся в таблицу `outbox`
в транзакции бизнес-данных; `BufferedEventPublisher` копит их за запрос,
UoW делает flush после commit; отдельный процесс `app/main_worker.py`
(OutboxRelay) публикует пачками (100 шт / 0.5 s) с `FOR UPDATE SKIP
LOCKED` — несколько relay не дублируют. Гарантия at-least-once:
обработчики идемпотентны по `event_id` (`RedisIdempotencyGuard`).

Кто публикует: HTTP-API/бот/воркеры через `enqueue_task`
(`legacy_worker.py::TaskPublisher`, robust-коннект, persistent-доставка;
пакетный `enqueue_many` с семафором 50 in-flight). Идемпотентность
прикладного уровня: `notified:{uid}:{lid}` (Redis, 7 дней), уникальные
индексы БД (payments, leads, partner_tx).

## 7. Realtime: Centrifugo v6

**Конфиг**: `configs/centrifugo.json.tmpl` (иерархический формат v6);
при старте контейнера sed подставляет `__SECRET__`/`__API_KEY__`/
`__INTERNAL__`/`__PROXY__` из env (значения = `CENTRIFUGO_SECRET`,
`CENTRIFUGO_HTTP_API_KEY`, `INTERNAL_TOKEN`, `http://gateway:8000`).
Namespaces `user`, `partner`, `thread` — все с
`subscribe_proxy_enabled` + `presence`.

**Каналы** (`app/application/chat/publisher.py`):

- клиент подписывается **только на свой личный канал**:
  `user:{telegram_id}` (Mini App) / `partner:{id}` (кабинет);
- `thread:{uuid}` существует в конфиге для legacy thread-режима
  (партнёрский кабинет), события туда больше не публикуются;
- ws-token: `GET /api/chats/ws-token` (HS256, sub=`user:{id}`|
  `partner:{id}`, TTL 12 ч; `app/infrastructure/services/centrifugo.py::
  centrifugo_jwt`).

**Subscribe-proxy**: Centrifugo на каждую SUBSCRIBE зовёт
`POST /api/chats/subscribe-proxy` со статическим `X-Internal-Token`
(равен `INTERNAL_TOKEN` бэкенда); `HandleSubscribeProxy`
(`app/application/chat/use_cases.py`): личный канал `user:{X}` разрешён
только sub==X; `thread:{uuid}` — только участникам. Отказ → centrifugo
отдаёт клиенту 403.

**События** (payload в `chatSocket.ts` / `use_cases.message_event`):

| type | Поля | Кто шлёт |
|---|---|---|
| `message` | thread_id, MessageView (id/sender_type/body/created_at) | SendMessage / SendOperatorMessage / бот |
| `read` | thread_id, user_type, last_read_message_id | MarkRead |
| `typing` | thread_id, user_type (TTL 4 с на клиенте) | SendTyping |
| `lot_expired` | thread_id, listing_id | экспиратор размещений (send-воркер) |
| `listing_status` | listing_id, status, reason? | lifecycle: submit/решение модерации/expired/archived (10 §3) |

**Публикация**: `ParticipantEventPublisher.publish_to_participants` —
собирает участников треда (владелец `user_id`, партнёр счёта, строки
`chat_participants` типов user/partner), дедуплицирует каналы и шлёт
копию события в каждый личный канал. `CentrifugoPublisher.publish` —
fire-and-forget (таймаут 3 s, любые ошибки → `logger.warning`, REST не
падает; клиенты доезжают поллингом). Онлайн-статусы — server-side
presence API (`CentrifugoPublisher.presence`; ключ не настроен — честный
«все офлайн»).

**Фронтовый стор** (`frontend/src/lib/chatStore.ts`, синглтон на сессию):

- `threads: Map<id, ChatThreadState>` (REST-вид + typing-set с TTL,
  lotExpired), `rooms: Map<id, ChatRoomState>` (страница сообщений,
  has_more, partnerReadId для ✓✓), `pendingUnread` — непрочитанные в
  ещё незагруженных тредах;
- `handleEvent`: message в открытый тред → append + авто-read чужого;
  в закрытый → unread++; незнакомый тред → тихий refresh списка не чаще
  10 с; listing_status **не** складывается в стор — только подписки
  `onListingStatus`/`useListingStatus` (Profile/SellCar);
- транспорт `chatSocket.ts`: WsTransport (официальный `centrifuge`,
  авто-reconnect) с поллинг-фоллбэком — 5 с: `GET /api/chats` + `after_id`
  открытого треда; фоллбэк включается флагом `VITE_CHAT_POLLING=1` либо
  если сокет не подключился за 5 с;
- отправка — всегда REST; стор только принимает события.
  `useSyncExternalStore` — один snapshot на экран Chats и бейдж таба
  «Чаты» в Shell (`useChatUnread`).

## 8. Фронтенд `frontend/src`

Сборка vite (Docker: `frontend/Dockerfile`, nginx отдаёт SPA и проксирует
`/api` → `http://backend:8000`; resolver 127.0.0.11 valid=10s —
пересоздание backend не даёт 502; index.html без кэша — Telegram-вебвью
кэширует намертво; HTTPS :8443 self-signed для LAN).

```text
frontend/src/
├── App.tsx            поверхности, авторизация, роутинг, keep-alive
├── main.tsx           вход
├── components/        Shell (таб-бар/сайдбар), ListingCard, Inspection, chat/ (ChatList, ChatRoom, Message)
├── screens/           экраны: Landing, Home (Лента|AI), Auto (Избранное|История|Мои), Feed,
│                      Listing (PDP), Filters, Calc, Compare, Profile, Chats, SellCar,
│                      MyLotsPanel, Onboarding, Login, AiSearch, Analyses, Favorites
├── partner/           PartnerApp + PartnerScreens + PartnerInspections (кабинет /partner)
├── admin/             AdminApp, AdminScreens, AdminChatRoom, ChatWidget (админка /admin)
├── lib/               chatStore, chatSocket, chatApi, api/client (REST+refresh),
│                      theme, viewHistory, fx, specs, marketPhotos, markdown, persistentState
├── api/               client.ts (fetch-обёртка, refresh-хук), auth.ts, ai.ts, types.ts
└── ui/                Toast, States, Skeleton, BackButton, PriceChart, LoginPrompt
```

### 8.1 Поверхности и лендинг (NAV-V2)

`App()`: `/partner*` → PartnerApp, `/admin*` → AdminApp (без Telegram-
авторизации); в браузере **«/» без query всегда лендинг** (даже если
surface выбран): карточки «Мини-апп»/«Кабинет партнёра», последний выбор
(`autobot.surface`) только сортирует и ставит метку «● последний вход»,
ничего не запирает; `chooseSurface` входит без перезагрузки, кнопка
«Сменить поверхность» в Профиле (`resetSurface`) — снова лендинг.
Telegram и deep-links с query (`/?make=…`) — сразу мини-апп.

### 8.2 Роутинг мини-аппа

- **BrowserRouter (без hash)**: пути `/`, `/auto`, `/chats`, `/profile`,
  стек-карточка `/listing/:id`, оверлей комнаты `/chats/:id`, `/filters`,
  `/calc`, `/compare`, `/sell`, `/login` (гость).
- Редиректы-совместимость (`OverlayRoutes`): `/ai` → `/`,
  `/favorites` → `/auto?tab=favorites`, `/analyses` →
  `/auto?tab=history` (query сохраняется — «похожие»).
- Deep-link из чата: `t.me/<bot>?startapp=listing_<id>` →
  `StartParamRedirect` → `/listing/{id}`.
- Мусорные пути → редирект на `/`.

### 8.3 Keep-alive экранов

`KEEPALIVE_TABS` (App.tsx): `/` (Home), `/auto`, `/filters`, `/calc`,
`/compare`, `/profile`, `/chats` — монтируются лениво при первом заходе
и дальше живут в DOM, неактивные прячутся классом `.keepalive-hidden`
(display:none) — эффекты не перезапускаются, данные/скролл не теряются
(`ScrollManager` сохраняет scrollY каждого таба). Карточка лота —
отдельный keep-alive (`ListingKeepAlive`): экземпляр живёт после первого
открытия, новый id — новый экземпляр; на табе-источнике плашка
«Вернуться к карточке». `/chats/:id` — оверлей: активным остаётся таб
`/chats`. NOT keep-alive: `/sell` (форма должна сбрасываться).

### 8.4 Табы и темы

Мини-апп — 4 таба (`components/Shell.tsx`): Главная `/`, Авто `/auto`,
Чаты `/chats` (бейдж непрочитанных из chatStore, гостю скрыт),
Профиль `/profile`; десктоп — те же пункты сайдбаром. Главная —
сегмент «Лента | AI-поиск» (обе панели в keep-alive), FAB «+ Продать»
только на `/`. Авто — сегменты Избранное/История/Мои объявления
(активный в `?tab=`, у гостя нет «Моих»). Партнёрский кабинет
(`partner/PartnerApp.tsx`): 9 разделов; мобайл — 5 табов
(Обзор/Пул/Мои/Чаты/«Ещё») + bottom-sheet (Рынок/Витрина/Объявления/
Чеки/Биллинг, активный подсвечивает таб «Ещё» точкой); десктоп — все 9
списком. Темы: light/dark/system (`lib/theme.ts`, применяются к AppRoot
telegram-ui + CSS-переменные).

### 8.5 Авторизация

`api/auth.ts`: в Telegram — `initData` → `POST /api/auth/telegram`
(access+refresh JWT); в браузере без сессии — гостевой вход
`POST /api/auth/guest` (демо: лента/карточка/калькулятор; лимит 5 гостевых
сессий/IP/мин); веб-вход — код через бота (`/api/auth/telegram/link`).
Смерть сессии → тихий re-authorize/гость (экран входа не показывается).

## 9. Конфигурация

Единственный источник — `Settings` в `app/config.py` (pydantic-settings,
env/`.env`, `SecretStr` для секретов, `extra=ignore`). Прод-fail-fast
(`_prod_secrets_required`): запрет дев-дефолта `APP_SECRET_KEY`, ключа
< 32 символов, пустого `INTERNAL_TOKEN`, dev-значений Centrifugo,
localhost-URL БД/Redis, guest:guest RabbitMQ; LLM-провайдер требует
ключ. Зеркало с комментариями — `.env.example`.

### 9.1 App / Bot

| Переменная | Тип | Дефолт | Назначение |
|---|---|---|---|
| `APP_ENV` | dev\|test\|staging\|prod | dev | окружение (алиас production→prod, регистр нормализуется) |
| `APP_SECRET_KEY` | SecretStr | `<dev-default>` | подпись JWT (прод: ≥32 симв., не дефолт) |
| `INTERNAL_TOKEN` | SecretStr | "" | X-Internal-Token админки/internal; пусто → fallback APP_SECRET_KEY (только dev) |
| `APP_BASE_URL` | str | http://localhost:8000 | адрес API для бота |
| `MINIAPP_URL` | str | http://localhost:8080 | публичный URL миниаппа (web_app-кнопка) |
| `BOT_TOKEN` | SecretStr | "" | токен Telegram-бота |
| `BOT_USERNAME` | str | "" | юзернейм бота (без @) для веб-входа; пусто → 503 |
| `ADMIN_CHAT_ID` / `MANAGER_CHAT_ID` | int\|None | None | алерты/эскалации |

### 9.2 Хранилища / Auth

| Переменная | Дефолт | Назначение |
|---|---|---|
| `DATABASE_URL` | postgresql+asyncpg://app:app@localhost:5432/autoselect | Postgres (async) |
| `REDIS_URL` | redis://localhost:6379/0 | кэш, rate, локи, FSM |
| `RABBITMQ_URL` | amqp://guest:guest@localhost:5672/ | задачи |
| `JWT_ALGORITHM` | HS256 | (HS256/384/512) |
| `ACCESS_TOKEN_TTL_SEC` / `REFRESH_TOKEN_TTL_SEC` | 3600 / 2592000 | TTL пары токенов (refresh > access — валидатор) |
| `INIT_DATA_MAX_AGE_SEC` | 86400 | максимум возраста initData (≤30 дней) |
| `GUEST_IP_RATE_LIMIT` | 5 | гостевых сессий с IP в минуту |

### 9.3 LLM / Диалог / RAG

| Переменная | Дефолт | Назначение |
|---|---|---|
| `LLM_PROVIDER` | glm | glm\|anthropic\|ollama\|mock |
| `GLM_API_KEY` / `ANTHROPIC_API_KEY` | — | ключи (в prod обязательны для своего провайдера) |
| `GLM_BASE_URL` | https://api.z.ai/api/paas/v4 | эндпоинт (для open.bigmodel.cn — свой) |
| `LLM_DEFAULT_MODEL` / `LLM_ADVANCED_MODEL` / `LLM_BASE_URL` / `LLM_TEMPERATURE` | glm-4.6 / glm-4.6 / None / 0.2 | модели, прокси, температура |
| `DIALOG_TTL_SEC` / `DIALOG_HISTORY_LEN` / `FAQ_CACHE_TTL_SEC` | 86400 / 8 / 86400 | AI-диалог |
| `RAG_SERVICE_URL` | None | пусто = RAG выключен → LLM-fallback |
| `RAG_API_TOKEN` | — | Bearer к rag_api |
| `RAG_ENABLED` | true | feature flag |
| `RAG_TIMEOUT_SEC` / `RAG_CACHE_TTL_SEC` | 5 / 3600 | таймаут/кэш (в compose для backend 120 s) |
| `RAG_CONFIDENCE_THRESHOLD` | 0.6 | ниже — LLM |
| `RAG_CB_FAIL_THRESHOLD` / `RAG_CB_COOLDOWN_SEC` | 5 / 300 | circuit breaker |

### 9.4 Уведомления / Партнёры

| Переменная | Дефолт | Назначение |
|---|---|---|
| `NOTIFY_RATE_LIMIT_SEC` | 30 | ≥1 сообщения / 30 c юзеру |
| `TELEGRAM_RATE_GLOBAL_PER_SEC` / `TELEGRAM_RATE_CHAT_PER_SEC` | 30 / 1 | token bucket TG (0 = выкл; Redis упал → fail-open) |
| `NOTIFY_SCAN_WINDOW_SEC` / `NOTIFY_SENT_TTL_SEC` | 1800 / 604800 | окно «свежих», антидубль |
| `DIGEST_MAX_CARDS` | 5 | карточек в дайджесте (1–10) |
| `NOTIFY_QUIET_HOURS_DEFAULT` | None | глобальные тихие часы "23:00-08:00" |
| `STALE_MAX_AGE_DAYS` | 3 | N дней без появления → is_active=false |
| `PARTNER_RATE_LIMIT_PER_MIN` | 60 | лимит запросов на API-ключ |
| `PARTNER_CPL_RATES` | {starter:5.0, pro:3.0, salon:2.0} | USD за лида (индивидуальный cpl_rate перекрывает) |

### 9.5 Перевод

| Переменная | Дефолт | Назначение |
|---|---|---|
| `TRANSLATE_ENABLED` | false | флаг сервиса перевода |
| `TRANSLATE_SERVICE_URL` | "" | http://translate:8200; пусто → оригинал |
| `TRANSLATE_API_TOKEN` | — | Bearer (= API_TOKEN сервиса) |
| `TRANSLATE_TIMEOUT_SEC` / `TRANSLATE_BATCH_SIZE` | 180 / 32 | NLLB на CPU сериализован локом |
| `TRANSLATE_CACHE_TTL_SEC` | 2592000 | tr:{tgt}:{src}:{sha1} |
| `TRANSLATE_CB_FAIL_THRESHOLD` / `TRANSLATE_CB_COOLDOWN_SEC` | 3 / 300 | CB |
| `TRANSLATE_TARGET_LANG` | rus_Cyrl | NLLB-код цели |
| `TRANSLATE_SOURCE_LANGS` | {encar:kor_Hang, kcar:kor_Hang, che168:zho_Hans} | код исходника по источнику ("auto" — детект) |
| (сервис) `TRANSLATE_MODEL` / `TRANSLATE_MAX_RSS_MB` | facebook/nllb-200-distilled-600M / 2950 | модель и RSS-сторож |

### 9.6 S3 / прокси / микро / Centrifugo / рынок / мониторинг

| Переменная | Дефолт | Назначение |
|---|---|---|
| `RAW_PAYLOAD_ENABLED` | false | raw → S3 (требует endpoint+креды) |
| `S3_ENDPOINT_URL` / `S3_BUCKET` / `S3_ACCESS_KEY` / `S3_SECRET_KEY` / `S3_REGION` | "" / raw-payloads / "" / "" / us-east-1 | MinIO/S3 |
| `RAW_RETENTION_DAYS` / `RAW_PRESIGN_TTL_SEC` | 180 / 600 | retention-крон, presign админки |
| `S3_PHOTOS_BUCKET` / `S3_PUBLIC_URL` | market-photos / http://localhost:9000 | фото рынка (public-read) |
| `MARKET_PHOTO_MIRROR_LIMIT` / `MARKET_MIRROR_BATCH` | 5 / 40 | фото на лот / лотов за прогон mirror |
| `PROXY_DATAIMPULSE_USER`+`PASS` (пара) | — | прокси-шлюз gw.dataimpulse.com:823 для источников с use_proxy |
| `PROXY_DATAIMPULSE_STICKY_TTL_SEC` / `_FAIL_THRESHOLD` / `_FAIL_COOLDOWN_SEC` | 1800 / 5 / 300 | sticky-окно, пауза шлюза |
| `GATEWAY_TIMEOUT_SEC` | 150 | прокси gateway (LLM-разборы) |
| `SVC_IDENTITY_URL` … `SVC_CHAT_URL` | localhost:8101…8107 | адреса доменных сервисов |
| `CENTRIFUGO_URL` / `CENTRIFUGO_PUBLIC_URL` | http://localhost:8008 / "" | внутренний publish / ws для браузера |
| `CENTRIFUGO_SECRET` / `CENTRIFUGO_HTTP_API_KEY` | `<change-me>` | подпись ws-JWT / publish-API (прод: не change-me) |
| `MARKET_FREE_DAYS` / `MARKET_WEEK_STARS` | 7 / 20 | бесплатный период дней / цена недели Stars (4 нед = x3) |
| `MARKET_USER_MAX_PAID_LOTS` | 3 | платных (Stars) лотов сверх бесплатного/включённого |
| `MARKET_FREE_COOLDOWN_DAYS` | 30 | кулдаун free-периода (анти-ротация) |
| `MARKET_PARTNER_PACKAGES` | {"10":500,"50":1800,"200":5000} | пакеты размещений партнёров (₽) |
| `MARKET_FORBIDDEN_WORDS` | [авито, drom, крипта, …] | словарь автомодерации (RU+EN) |
| `ALERTS_ENABLED` / `ALERT_DEDUP_TTL_SEC` / `DLQ_ALERT_THRESHOLD` / `HEALTH_CHECK_INTERVAL_SEC` / `SOURCE_CB_ERROR_STREAK` | true / 1800 / 10 / 300 / 5 | алерты и CB |
| `SENTRY_DSN` / `LOG_LEVEL` | None / INFO | мониторинг |
| `USE_CASE_FAVORITES` | true | legacy-toggle избранного через use case |
| (compose) `DB_PASSWORD`, `MINIO_IMAGE`, `LAN_IP`, `RAG_*`, `ENCRYPT_DOCUMENTS`, `PQ_ENCRYPTION_*` | — | интерполяция docker-compose / RAG / PQ-крипта |
| (frontend) `VITE_API_BASE` / `VITE_CHAT_POLLING` | /api / — | база API, форс поллинга чатов |

## 10. Резильентность: профили compose и деградация

`docker-compose.yml` — ступенчатые профили (база поднимается всегда —
у postgres/redis профилей нет):

| Ступень | Команда | Что появляется | Что деградирует без следующей |
|---|---|---|---|
| base | `docker compose up -d` | postgres, redis | — (критичный минимум) |
| core | `--profile core` | backend (монолит), bot, miniapp | продукт жив: лента/поиск/тарифы/бот/рынок/чаты (поллинг); без rabbit — не крутятся fetch/уведомления |
| storage | `--profile storage` | minio | фото-загрузки и S3-зеркало (без — 503 photos_unavailable / raw NULL) |
| workers | `--profile workers` | rabbitmq, worker_fetch, worker_send, translate | свежие объявления, переводы |
| realtime | `--profile realtime` | centrifugo, chat-сервис | мгновенные чаты/статусы, presence (фоллбэк-поллинг) |
| ai | `--profile ai` | dialog, qdrant, rag_* | RAG-бот (остаётся LLM-fallback) |
| micro | `--profile micro` | gateway + 7 доменных сервисов | декомпозиция вместо монолита |
| mono | `--profile mono` | backend + bot + miniapp + rabbit + воркеры + minio + translate | всё в одном профиле |

- `depends_on` c `required: false` для rabbitmq у backend/воркеров/
  bot/dialog/admin — HTTP-ядро стартует без брокера (воркеры догонят);
  критичны только postgres+redis (`/health/detailed` даёт 503 лишь
  когда лежит БД).
- Профили mono/micro не смешивать: gateway и backend делят хост-порт
  8000 и сетевой алиас `backend` — конфликт бинда вместо тихих 502.
- Circuit breaker'ы в коде: источники (5 ошибок подряд → skip),
  RAG-клиент, translate-клиент, прокси-шлюз (пауза 5 мин → прямые
  запросы). Fire-and-forget: публикация Centrifugo, ParserDoctor,
  S3-очистка исчезнувших лотов.
- Память в compose: qdrant 2 g, rag_worker 1500 m (embedding-модель +
  шифрованные чанки), translate 3500 m + RSS-сторож 2950 MB (утечка
  NLLB → самовыход → рестарт; порог выше легитимного пика ~2.9 GB).
- Прод: `deploy/docker-compose.prod.yml` (Caddy + Let's Encrypt,
  порты миниаппа закрыты — весь трафик через caddy → miniapp → /api);
  публичный URL миниаппа для Telegram — туннель `miniapp-url.bat`
  (localhost.run; trycloudflare блокируется провайдером).

## 11. Наблюдаемость и тесты (кратко)

- Health: `/health` (liveness), `/health/detailed` (postgres/redis/
  rabbitmq; 503 только при лежащей БД), у каждого микросервиса свой
  `/health`; воркеры/бот — heartbeat-файлы
  (`app/infrastructure/runtime/heartbeat`).
- Метрики: `GET /metrics` (X-Internal-Token) — юзеры/фильтры/объявления/
  уведомления/тарифы/health источников; воронка/retention —
  `FunnelDashboard`/`RetentionDashboard`.
- Алерты в Telegram (`ADMIN_CHAT_ID`) с дедупом `alert:{key}`: DLQ ≥ 10,
  поломка парсера, CB источника/перевода/прокси/RAG, приостановка
  партнёра. Sentry — `SENTRY_DSN`.
- Тесты (`docs/TESTING.md`): pytest ~1362 тест-функции в `tests/`,
  зеркально архитектуре (`identity/ catalog/ billing/ partners/ dialog/
  admin/ bot/ security/ transport/ infrastructure/ architecture/`);
  фронт — vitest (~552). CI: GitHub Actions (pytest + ruff + pip-audit
  + bandit).

## 12. Карта документации

| Документ | Содержание |
|---|---|
| [ADR.md](ADR.md) | реестр архитектурных решений (Контекст/Решение/Последствия) |
| [API.md](API.md) | HTTP API |
| [NAV-V2.md](NAV-V2.md) | спека навигации v4 + лендинг (2026-10-28) |
| [OPERATIONS.md](OPERATIONS.md) | запуск, env, мониторинг, прод-чеклист |
| [MONETIZATION.md](MONETIZATION.md) | тарифы/Stars/CPL |
| [AI.md](AI.md) | AI-контур (LLM, RAG, промпты) |
| [SECURITY.md](SECURITY.md), [INCIDENTS.md](../INCIDENTS.md) | модель угроз и история инцидентов |
| [TESTING.md](TESTING.md), [RUNBOOK.md](RUNBOOK.md) | тесты и регламенты |
| [SOURCES.md](SOURCES.md) | источники данных |
