# Архитектура

## Слои (Clean Architecture)

```
app/
├── domain/            # сущности и чистая логика (не импортирует app/*)
│   ├── listings/ search/ favorites/ dialog/ partners/ limits/ pricing/
│   ├── analytics/ audit/ calc/ health/ notifications/ scheduling/
│   ├── sources/ subscription/ translation/ shared/
├── application/       # сценарии (use cases) + контракты сервисов
├── infrastructure/    # адаптеры: db, cache, security, services, sources,
│   │                  # storage (S3), translation, workers, messaging, ai
├── transport/         # входные точки: http (FastAPI), bot (aiogram),
│   │                  # messaging (FastStream-обработчики)
└── di/                # dishka: container.py + providers/*
```

Правило зависимостей: `domain` ничего не импортирует из `app`; `application`
зависит только от `domain`; `infrastructure` реализует порты `application`;
`transport` и `di` собирают всё вместе. Проверяется тестом
`tests/architecture/test_layers.py`.

## Прикладной слой: 6 микросервисов

Сценарии сгруппированы по доменам; **контракты** каждого сервиса — явные:

- `<service>/ports.py` — интерфейсы-порты (`typing.Protocol`), которые
  реализует инфраструктура;
- `<service>/dto.py` — DTO и команды сценариев.

Кросс-сервисные порты (нужны 2+ сервисам) — в `application/shared/ports.py`
(`CachePort`, `LLMPort`, `ClockPort`, `UnitOfWork`).

| Сервис | Контракты | Сценарные модули (`app/application/…`) | Роутеры (transport/http/routers) |
|---|---|---|---|
| **identity** | identity/ports, identity/dto | auth, users, profile | auth, users, me |
| **catalog** | catalog/ports, catalog/dto | listings (+ingest), filters, favorites, notifications (+settings), analysis, pricing, search, translation | listings, filters, favorites, notifications, analysis |
| **billing** | billing/ports, billing/dto | payments, subscription | payments, subscription |
| **partners** | partners/ports, partners/dto | partners (use_cases, queries), leads | partners, leads |
| **dialog** | dialog/ports, dialog/dto | dialog, ai_search, quota | dialog, ai_search |
| **admin** | admin/ports, admin/dto | admin (use_cases, assistant), metrics, parser_doctor, analytics, audit, alerts | admin, metrics |

Сквозные утилиты: `calc` (калькулятор расходов — бот + HTTP), `errors.py`
(иерархия `AppError`), `shared/` (общие порты).

Коллизии имён разрешены разделением портов: auth-овский `UserRepository`
(find/create/update) и users-овский `UserUpsertRepository` (только upsert);
CRM-`LeadView` в partners/dto и партнёрский `PartnerLeadView` в queries.

## DI (dishka)

- `app/di/container.py` собирает провайдеры: `settings`, `database`, `redis`,
  `broker`, `security`, `adapters`, `repositories`, `use_cases`.
- **HTTP** — `dishka-fastapi`, REQUEST-скоуп; `FastAPI.Depends` не используется:
  зависимости приходят в сигнатурах эндпоинтов из dishka-контекста
  (`app/transport/http/deps/`), protected-роуты — через `InternalDishkaRoute`
  (X-Internal-Token проверяется до резолва сценариев).
- **Bot** — dishka-aiogram; **messaging** — dishka-faststream.

## Транспорт и очередь

- `transport/http/routers/*` — REST; OpenAPI на `/docs`.
- `transport/bot` — aiogram 3: handlers, button_flow, client; long polling.
- `transport/messaging` — контракты команд (строгие Pydantic-модели,
  `extra="forbid"`, обязательный `idempotency_key`) и обработчики
  (handlers/enrich).
- `infrastructure/messaging` — publisher (постановка), outbox-релей,
  идемпотентность доставки, топология, legacy-воркер.
- `infrastructure/workers` — fetch/send: legacy-контур `TaskWorker`/`CronSpec`
  (aio-pika: retry, DLQ, cron внутри воркера) + новый FastStream-контур.
  Оба профиля сосуществуют; новые задачи — только с `idempotency_key`.

## Микросервисы (профиль `micro`)

Один код `app/` разворачивается либо монолитом (`app.main_api`, профиль
mono), либо шестью доменными сервисами (`services/<name>/main.py`) за
gateway'ем:

| Сервис | Порт | Домены |
|---|---|---|
| `gateway` | 8010→8000 (алиас `backend` в сети) | маршрутизация `/api/*` |
| `identity` | 8101 | auth, users, me |
| `catalog` | 8102 | listings, favorites, filters, notifications |
| `billing` | 8103 | payments, subscription |
| `partners` | 8104 | partners (CPL), leads |
| `dialog` | 8105 | dialog, ai_search |
| `admin` | 8106 | admin, аналитика, метрики |
| `rag` | 8100 | RAG-ядро (services/rag) |
| `translate` | 8200 | NLLB-перевод (services/translate) |

Ключевые решения:

- **Ноль правок у потребителей.** Gateway слушает :8000 внутри сети и имеет
  алиас `backend` — бот (`APP_BASE_URL=http://backend:8000`) и nginx Mini App
  не меняются.
- **Аутентификация на границе домена.** Gateway только маршрутизирует;
  `X-Internal-Token`, `Authorization`, `X-API-Key` проходят сквозь и
  проверяются доменными сервисами.
- **Специфичные маршруты раньше общих**: `/api/users/{id}/subscription` →
  billing (до `/api/users` → identity), `/api/admin/partners` → partners
  (до `/api/admin` → admin). Таблица покрыта юнит-тестами
  (`tests/transport/test_gateway_routing.py`).
- **Отказ домена ≠ отказ платформы**: недоступный сервис → 502 с именем
  сервиса; `GET /health/detailed` агрегирует пинги всех шести.
- **`GET /metrics`** (вне `/api/*`) пробрасывается в admin-домен — мониторинг
  одинаков в обоих профилях.

## Данные

- **PostgreSQL 16** (`app/infrastructure/db`): users, user_filters, sources,
  source_health, listings (+price history, translations, tracking), favorites,
  sent_log, analytics_events, leads (+partner-поля, qualification),
  partner_accounts, partner_api_keys, partner_transactions, payments,
  admin_actions, web_auth. Миграции Alembic `0001–0014`
  (`app/infrastructure/db/migrations`).
- **Redis 7**: FSM бота, DialogState (TTL 24 ч), дедуп, rate limits
  (Token Bucket на Lua), кэши LLM/RAG/перевода, идемпотентность очередей.
- **RabbitMQ 3**: очереди `fetch`/`send` + DLQ; RAG: query/ingest/delete.
- **Qdrant** — векторная база RAG; **MinIO (S3)** — сырые ответы источников
  (retention 180 дней, presigned GET 10 мин).
