# 09. Слоистая структура `app/`

**Дата:** 21.10.2026
**Статус:** физическая перекладка выполнена, порты/DTO/ошибки введены,
1138 тестов зелёные.

---

## 1. Дерево

```text
app/
├── domain/                     # чистый Python, только stdlib
│   ├── dialog/state.py         # DialogState, Message
│   ├── favorites/ports.py      # FavoriteRepository, FavoriteItem, Cursor
│   ├── listings/
│   │   ├── errors.py           # ListingNotFound
│   │   ├── ports.py            # ListingRepository
│   │   └── unified.py          # UnifiedListing, FetchResult
│   ├── search/criteria.py      # SearchCriteria, SourceConfig
│   └── shared/                 # DomainError, DomainEvent
│
├── application/                # сценарии; импортирует только domain
│   ├── dto/rag.py              # контракт RAG-ответа
│   ├── favorites/use_cases.py  # Toggle/List/Is/Remove + ListFavoriteCards
│   └── shared/                 # UnitOfWork/Publisher порты, Page, Cursor
│
├── infrastructure/             # реализации портов и I/O
│   ├── ai/                     # llm_client, rag_client, agent, tools, prompts
│   ├── db/                     # models, session, uow, outbox, repositories, migrations
│   ├── messaging/              # topology, publisher, relay, idempotency, legacy_worker
│   ├── security/               # tokens (JWT/initData), pq_crypto, partner_keys
│   ├── services/               # трансляция legacy-логики (23 модуля)
│   ├── sources/                # encar, kcar, che168, registry, common
│   └── workers/                # legacy fetch/send воркеры
│
├── transport/                  # только I/O
│   ├── bot/                    # aiogram
│   ├── http/
│   │   ├── deps/               # auth, internal, partner, use_cases
│   │   ├── middleware/trace.py
│   │   ├── routers/            # 17 контроллеров
│   │   └── schemas/            # Pydantic request/response
│   └── messaging/              # contracts + handlers/enrich
│
├── di/                         # composition root
│   ├── container.py
│   └── providers/              # settings, database, redis, repositories, use_cases, broker
│
├── config.py                   # Settings
├── main.py                     # shim для `uvicorn app.main:app`
├── main_api.py                 # сборка FastAPI
├── main_worker.py              # outbox relay
└── main_consumer.py            # FastStream consumer
```

Удалены как отдельные пакеты: `app/api`, `app/core`, `app/db`, `app/ai`,
`app/adapters`, `app/schemas`, `app/services`, `app/queue`, `app/workers`,
`app/bot`.

---

## 2. Правило зависимостей

```
transport ──► application ──► domain
     │              ▲
     │              │ implements ports
     └──────► infrastructure ──► domain
                    ▲
                   di  (единственное место, где встречаются все слои)
```

Проверка (выполнена, 0 нарушений):

```python
# domain / application не содержат ни одного импорта
# app.infrastructure, app.transport, app.di, app.main_api
```

| Слой | Может импортировать | Не может |
|---|---|---|
| `domain` | стандартная библиотека | всё остальное |
| `application` | `domain` | `infrastructure`, `transport`, `di` |
| `infrastructure` | `domain`, `application` (порты), внешние библиотеки | `transport`, `di` |
| `transport` | `application` (цель), схемы, FastAPI/aiogram | бизнес-правила и SQL |
| `di` | все слои | — |

---

## 3. Карта переноса

| Было | Стало |
|---|---|
| `app/api/*.py` | `app/transport/http/routers/*.py` |
| `app/api/deps_use_cases.py` | `app/transport/http/deps/use_cases.py` |
| `app/schemas/*.py` | `app/transport/http/schemas/*.py` |
| `app/core/config.py` | `app/config.py` |
| `app/core/security.py` | `app/infrastructure/security/tokens.py` |
| `app/core/pq_crypto.py` | `app/infrastructure/security/pq_crypto.py` |
| `app/core/deps*.py` | `app/transport/http/deps/{auth,internal,partner}.py` |
| `app/core/trace.py` | `app/transport/http/middleware/trace.py` |
| `app/db/*` | `app/infrastructure/db/*` |
| `app/adapters/*` | `app/infrastructure/sources/*` |
| `app/ai/*` | `app/infrastructure/ai/*` |
| `app/services/*` | `app/infrastructure/services/*` |
| `app/queue/rabbit.py` | `app/infrastructure/messaging/legacy_worker.py` |
| `app/workers/*` | `app/infrastructure/workers/*` |
| `app/bot/*` | `app/transport/bot/*` |
| `app/main.py` | `app/main_api.py` (+ shim `app/main.py`) |
| `schemas/listing.UnifiedListing` | `domain/listings/unified.py` |
| `schemas/criteria.py` | `domain/search/criteria.py` |
| `schemas/dialog.py` | `domain/dialog/state.py` |
| `schemas/rag.py` | `application/dto/rag.py` |

Импорты переписаны в 147+ файлах; `alembic.ini`, `docker-compose.yml`,
`Dockerfile`, `start.*`, `deploy/*` обновлены.

---

## 3.1. Порты, DTO, ошибки

- `application/ports/__init__.py` — `CachePort`, `ObjectStoragePort`,
  `LLMPort`, `RAGPort`, `NotifierPort`, `ClockPort` (все `Protocol`);
- `application/dto/listings.py` — `ListingCard` (dataclass, без ORM/HTTP);
- `application/errors.py` — `AppError`/`NotFound`/`Forbidden`/`Conflict`;
  use case переводит доменные ошибки в прикладные, транспорт знает только
  `AppError`;
- `tests/architecture/test_layers.py` — 4 теста, которые падают при любом
  импорте `application` → `infrastructure/transport/di` или при SQLAlchemy/
  FastAPI/httpx в application.

## 3.2. Декомпозиция сервисов

Чистые правила вынесены из сервисов в домен, I/O остался в инфраструктуре,
транспорт получил тонкие обёртки:

| Было (infra/services) | Домен | Приложение |
|---|---|---|
| `subscription.py` | `domain/subscription/policies.py` | `application/subscription/use_cases.py` |
| `cron.py` | `domain/scheduling/cron.py` | — |
| `translate_glossary.py` | `domain/translation/glossary.py` | — |
| `delivery.py` | `domain/calc/delivery.py` | `application/calc/use_cases.py` |
| `rate_limit.bucket_advance` | `domain/limits/token_bucket.py` | — |
| `source_health` (CB-политика) | `domain/sources/health.py` | — |
| `notify.py` (8 чистых функций) | `domain/notifications/rules.py` | `application/notifications/use_cases.py` |
| `fetch.py` (ingestion) | `domain/listings/ingestion.py` | `application/listings/ingest.py` |
| `partners.py` (деньги) | `domain/partners/pricing.py`, `domain/partners/ports.py` | `application/partners/use_cases.py` |
| `dialog.py` (FAQ/состояние) | `domain/dialog/faq.py`, `domain/dialog/state.py` | — |

`application/calc/use_cases.py` и `application/subscription/use_cases.py` —
тонкие обёртки без I/O: транспорт больше не импортирует домен.

Осталось разложить 12 сервисов с реальным I/O (`fetch`, `notify`, `dialog`,
`partners`, `price_analysis`, `parser_doctor`, `storage`, `alerting`, …):
каждый требует репозиториев/портов и переписанных вызовов.

## 3.3. Второй батч декомпозиции

- `alerting` → `infrastructure/messaging/telegram.py` + `application/alerts`;
- `translate_client` → `infrastructure/translation/client.py` + `application/translation`;
- `storage` → `infrastructure/storage/s3.py` (реализует `ObjectStoragePort`);
- `ai_quota` → `infrastructure/cache/quota.py` + `application/quota`;
- `search` → `domain/search/query.py` + `infrastructure/db/repositories/listings_search.py`;
- `analytics`/`funnels` → `domain/analytics` + репозиторий;
- `admin_audit`/`users` → репозитории + `application/{audit,users}`;
- `price_analysis` → `domain/pricing/rules.py` + `application/pricing`;
- `parser_doctor` → `domain/health/triage.py` + `application/parser_doctor`;
- `admin_assistant` → `application/admin/assistant.py` (LLM через порт).

`infrastructure/services/*` теперь — фасады (35–105 строк) и адаптеры;
крупные остатки: `partners.py` (SQL-репозиторий), `fetch.py` (сборка),
`notify.py`/`dialog.py` (Redis/SQL).

## 4. Что уже по-настоящему тонкое

`transport/http/routers/favorites.py` (113 строк) — эталон:

- ни одного `SessionDep`, SQL или `commit()`;
- `list` → `ListFavoriteCards` (порт избранного + порт карточек);
- `toggle` → `ToggleFavorite`;
- `is_favorite` → `IsFavorite`;
- `delete` → `RemoveFavorite`;
- ошибки домена маппятся в HTTP в одном месте.

---

## 5. Что осталось по контроллерам

17 роутеров всё ещё содержат legacy-логику (≈5 100 строк): `auth`, `listings`,
`partners`, `admin`, `dialog`, `analysis`, `ai_search`, `notifications`,
`metrics`, `payments`, `filters`, `calc`, `leads`, `me`, `users`.

Это фаза 4 плана: для каждого эндпоинта — use case в `application/`,
SQL — в репозиторий, роутер — 5–15 строк. Механика повторяет избранное.
Делать это одним коммитом нельзя: 68 эндпоинтов и денежные пути
(`partners`, `payments`) требуют отдельного прогона и наблюдения.

---

## 6. Как добавить эндпоинт правильно

1. Порт в `domain/<context>/ports.py`.
2. Use case в `application/<context>/use_cases.py` (только порты).
3. Реализация порта в `infrastructure/db/repositories/`.
4. Провайдер в `di/providers/repositories.py` и `use_cases.py`.
5. Роутер: `FromDishka[...]`/`Depends`, без SQL и `commit()`.
6. Тест use case на фейках + интеграционный тест репозитория.
