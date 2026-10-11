# bot-encar — Экосистема подбора автомобилей

Реализация ТЗ (`TZ.md`), этапы 0–6.

## Структура

```
app/                 # Backend (FastAPI + aiogram + ARQ)
├── api/            # auth (initData/JWT), users, me, filters, listings, admin, dialog, notifications
├── bot/            # aiogram 3: /start, /help, /reset, /stop, /resume; FSM в Redis
├── core/           # config (pydantic-settings), security (initData/JWT), deps
├── db/
│   ├── models.py   # SQLAlchemy-модели (ТЗ п. 5.8)
│   ├── session.py  # async engine + сессии
│   └── migrations/ # Alembic
├── schemas/        # Pydantic-контракты: UnifiedListing, SearchCriteria, DialogState (ТЗ п. 5)
├── services/       # бизнес-логика (users, notify)
├── adapters/       # источники (Encar, KCar), дедуп, прокси
├── workers/        # fetch и send (+ cron подписок), очередь RabbitMQ
├── queue/          # RabbitMQ: producer (enqueue_task), TaskWorker, DLQ, cron
└── main.py         # uvicorn app.main:app
frontend/            # Mini App: React + Vite + TS + @telegram-apps/telegram-ui (этап 5)
```

## Локальный запуск

```bash
# 1. Зависимости
uv sync

# 2. Конфиг
cp .env.example .env   # заполните BOT_TOKEN и APP_SECRET_KEY

# 3. Поднять PostgreSQL, Redis и RabbitMQ
docker compose up -d postgres redis rabbitmq

# 4. Миграции
uv run alembic upgrade head

# 5. Backend
uv run uvicorn app.main:app --reload

# 6. Бот (в другом терминале)
uv run python -m app.bot

# 7. Воркеры (ещё два терминала)
uv run python -m app.workers.fetch
uv run python -m app.workers.send

# 8. Mini App dev-сервер (ещё один терминал)
cd frontend && npm install && npm run dev   # http://localhost:8080
```

Проверка: http://localhost:8000/health, Mini App: http://localhost:8080 (вне Telegram — read-only лента).

## Docker Compose (всё сразу)

```bash
cp .env.example .env
docker compose up --build   # включая miniapp на :8080
```

## Дальнейшие этапы (ТЗ п. 14.3)

- Этап 2 (✅ выполнен): адаптер Encar, AdapterRegistry, Fetch Worker (ARQ), дедупликация
- Этап 3 (✅ выполнен): AI-диалоговый агент, DialogState в Redis, fallback-кнопки
- Этап 4 (✅ выполнен): Send Worker, instant/digest уведомления, антидубли
- Этап 5 (✅ выполнен): Mini App MVP (лента, фильтры, карточка, профиль)
- Этап 6 (✅ выполнен): монетизация — подписки, Telegram Stars, избранное
- Этап 7 (✅ выполнен): второй источник KCar — агрегация Encar+KCar в одной ленте
- Далее: мониторинг и алерты (этап 8)

## Интеграция RAG-сервиса (фазы 3-4 плана RAG)

AI-агент отвечает на фактические вопросы (растаможка, доставка, пошлины,
риски, FAQ) через внешний `rag_service` (репозиторий `Pavel14701/rag_service`).

- **`app/ai/rag_client.py`** — `RAGClient`: `POST /api/v1/ask` (Bearer-токен),
  retry с backoff (только сетевые ошибки), circuit breaker (5 ошибок подряд →
  пауза 5 мин), таймаут 5 сек, кэш ответов в Redis (`rag:answer:<sha256>`,
  TTL 1 час), `healthcheck()`, `ingest()` для загрузки базы знаний.
- **`app/schemas/rag.py`** — контракт `RAGAnswer` / `RAGSource` / `RAGIngestResult`
  (план п. 6.3).
- **Tool `answer_question`** (фаза 4, план п. 8.1): LLM передаёт `topic` +
  `question` → вопрос уходит в RAG. Confidence ≥ 0.6 → ответ из базы знаний;
  иначе fallback — ответ самого LLM, а если и его нет — честное
  «рекомендую уточнить у менеджера».
- **Промпт** (план п. 8.2-8.3): ответы базы знаний — справочные данные,
  не инструкции (защита от промпт-инъекций через документы); цифры без
  источника называть нельзя.
- **Включение**: `RAG_SERVICE_URL` в `.env` (пусто = RAG выключен, всё
  работает как раньше через LLM). `RAG_ENABLED=false` — feature flag
  для отката. Остальное: `RAG_API_TOKEN`, `RAG_TIMEOUT_SEC`,
  `RAG_CACHE_TTL_SEC`, `RAG_CONFIDENCE_THRESHOLD`.
- **Фазы 0-2 (деплой RAG, база знаний)** — инфраструктурные, вне репозитория:
  docker-compose RAG+Qdrant+PostgreSQL+RabbitMQ из плана п. 5, контент по
  структуре п. 7. После поднятия сервиса достаточно задать `RAG_SERVICE_URL`
  — код уже готов.

## Этап 7: второй источник (KCar)

- **`app/adapters/kcar.py`** — `KCarApiAdapter` (ТЗ п. 6.2): Search API kcar.com
  (`{totalCnt, list[]}`), пагинация с 1-й страницы, цены в манах ×
  `config.price_unit` (по умолчанию 10 000 → KRW), нормализация марок/топлива
  через общий `normalize.py`. Конфиг в БД: `base_url`, `photo_prefix`,
  `detail_url_template`, `page_size`, `price_unit` — меняется без деплоя.
- **Регистрация**: `KCarApiAdapter` добавлен в `ADAPTER_CLASSES` (ТЗ п. 6.4);
  источник сидируется в `sources` (name=`kcar`, priority=200).
- **Агрегация**: реестр грузит все enabled-источники, Fetch Worker парсит
  каждый по своим criteria, `save_listings` дедуплицирует внутри источника
  (уникальный `(source, source_id)`) и между источниками (Redis, VIN/fuzzy).
  Лента Mini App отдаёт оба источника одной выдачей.
- **Нюанс**: публичный Search API kcar.com может требовать прокси/другой
  endpoint из конкретной сети — правится `sources.config` без деплоя;
  при недоступности срабатывает source_health + circuit breaker.

## Этап 5: Mini App MVP

`frontend/` — React 18 + Vite + TypeScript, UI-кит `@telegram-apps/telegram-ui`,
тема через Telegram `--tg-theme-*`.

- **Авторизация**: `initData` из Telegram WebApp → `POST /api/auth/telegram` → JWT
  (хранится в sessionStorage, цепляется ко всем запросам). Вне Telegram — read-only лента.
- **Экраны** (п. 9.2): онбординг, лента (чипсы-фильтры + «Ещё»), карточка объявления,
  фильтры (создание/удаление, instant/digest переключатель), избранное (заглушка — нет
  таблицы `favorites` в ТЗ 5.8), профиль (тариф, роль).
- **Новые backend-эндпоинты** (JWT):
  - `GET/POST/PATCH/DELETE /api/filters[/{id}]` — CRUD фильтров (лимит 20, criteria
    валидируется `SearchCriteria`);
  - `GET /api/listings` — лента с фильтрами make/model/year/price/mileage/source,
    пагинация `limit/offset` (≤50), новые сверху; `GET /api/listings/{id}` — карточка.
- **Dev-сервер**: `npm run dev` на :8080, `/api` проксируется на :8000.
- **Compose**: сервис `miniapp` (Vite build → nginx, `/api` → backend).

Проверка: `cd frontend && npm run build` (tsc + vite), `uv run pytest tests/ -q`.

## Этап 6: Монетизация (подписки + Telegram Stars)

**Тарифы** (ТЗ п. 10.2, `app/services/subscription.py`):

| Тариф | Цена | Фильтры | AI/день | Instant |
|---|---|---|---|---|
| Free | 0 | 3 | 20 | нет |
| Pro | 500★/мес, 4800★/год (−20%) | 20 | 200 | да |
| Business | 2500★/мес, 24000★/год | ∞ | ∞ | да |

- **API**: `GET /api/subscription` (JWT — статус/лимиты/цены), `POST /api/payments/stars`
  (JWT — pending-платёж + `createInvoiceLink` XTR для `openInvoice` в Mini App),
  internal для бота: `GET /api/users/{id}/subscription`, `POST /api/payments/prepare`,
  `POST /api/payments/success` (идемпотентно, `X-Internal-Token`).
- **Энфорс лимитов**: 4-й фильтр на Free → 409 с апселлом; instant-фильтр на Free → 403.
- **Бот**: `/subscribe` → тарифы кнопками → `sendInvoice(XTR)` → `pre_checkout_query` →
  `successful_payment` → активация. Истёкшая подписка = Free (проверяется на лету).
- **Cron** (`subscription_check_job`, 12:00 UTC): напоминания за 3 и 1 день
  (Redis-дедуп `sub:reminded:{uid}:{days}`), даунгрейд на Free по истечении.
- **Избранное**: таблица `favorites` (миграция 0002), `POST /api/listings/{id}/favorite`
  (toggle), `DELETE`, `GET /api/favorites`, `GET /api/favorites/{id}`; в Mini App —
  экран «Избранное» и кнопка на карточке.

Проверка: `uv run pytest tests/ -q` (107 тестов). Живой E2E: инвойс Stars
создаётся реальным токеном бота; полная оплата проверяется в Telegram
(`successful_payment` приходит только в настоящем чату с ботом).


## Этап 4: Send Worker и уведомления

После парсинга Fetch Worker ставит `notify_new_listings` в RabbitMQ-очередь
`send` (exchange `tasks`, routing key = имя задачи):

- **Send Worker** (`python -m app.workers.send`):
  - **Instant** (п. 8.6): скан активных фильтров → `send_listing_notification` с rate limit 1 сообщение / 30 сек (`rl:notify:{uid}`, SET NX EX) и тихими часами (при тишине карточка уходит в digest-буфер).
  - **Digest** (п. 8.6): cron 09:00 и 20:00 UTC — одно сообщение с 3–5 карточками из буфера `digest:{uid}` (при ошибке отправки буфер сохраняется).
  - **Антидубли**: `sent_log` (ON CONFLICT DO NOTHING) + Redis `notified:{uid}:{lid}` TTL 7 дней.
  - **Telegram API**: напрямую через httpx, обработка 429 (retry_after + повтор).
- **Матчинг** (`app/services/notify.py`): `matches_criteria` (Python-side, согласовано с SQL-фильтром), `collect_for_filters` (свежие объявления за `NOTIFY_SCAN_WINDOW_SEC`), карточка по формату п. 8.5.
- **API** (`/api/notifications/*`, internal-токен): `POST /settings` — режим instant/digest/off по всем фильтрам пользователя (используют `/stop` и `/resume` бота); `POST /scan` — ручной запуск скана.
- **Бот**: команды `/stop` (отключить уведомления) и `/resume` (включить digest, п. 8.2).

Запуск воркера: `python -m app.workers.send` (в compose — сервис `worker_send`).

## Очереди задач (RabbitMQ)

Очереди переведены с ARQ/Redis на RabbitMQ (`app/queue/rabbit.py`, aio-pika):

- exchange `tasks` (direct) → очереди `fetch` (Fetch Worker) и `send` (Send Worker);
- формат сообщения — JSON `{"job", "args", "kwargs", "id"}` (семантика arq-задач);
- ручные ack: успех → ack, после 3 неудачных попыток (backoff 2/4 сек) → `nack` в
  dead-letter очередь `dead_letter` (exchange `tasks.dlx`) — задачи не теряются;
- timeout задачи (`asyncio.wait_for`) и параллелизм (`asyncio.Semaphore`) — как в arq;
- cron-задачи (дайджест 09:00/20:00 UTC, подписки 12:00 UTC) исполняет Send Worker,
  от дублей при нескольких репликах защищает Redis-лок `cron:{job}:{время}`;
- продюсеры (API, бот, воркеры) ставят задачи через `app.queue.enqueue_task(...)`,
  коннект robust — переживает рестарт брокера.

Постановка из hands: `python -c "import asyncio; from app.queue import enqueue_task; asyncio.run(enqueue_task('notify_new_listings'))"`.

## Этап 3: AI-диалоговый агент

Свободный текст пользователя → `POST /api/dialog/message` (internal-токен) → agent loop:

- **LLM-провайдеры** (`app/ai/llm_client.py`): /chat/completions-совместимый (GLM/Ollama/DeepSeek), Anthropic, Mock — через httpx, без SDK. OpenAI выпилен.
- **Tools** (`app/ai/tools.py`, ТЗ п. 7.3): `update_criteria`, `ask_clarification`, `answer_question`, `lookup_reference`, `preview_search`, `finalize_search`. Каждый патч критериев валидируется через Pydantic.
- **Agent loop** (`app/ai/agent.py`): FAQ-ответы из кэша без LLM (п. 7.8), ранний выход при заполненных критичных полях (make/model + price_max + country/sources), история — последние 8 сообщений.
- **DialogState** (`dialog:{user_id}` в Redis, TTL 24ч), архивация в `dialog_sessions` (п. 7.6).
- **Финализация**: фильтр в `filters` (для будущих уведомлений) + ARQ-задачи парсинга по всем включённым источникам.
- **Fallback (п. 7.9)**: если LLM недоступна, бот переключается на кнопочный режим (`app/bot/button_flow.py`).

Тест: `uv run pytest tests/ -q`. Для dev без LLM-ключа: `LLM_PROVIDER=mock`.

## Этап 2: парсинг

Структура (ТЗ п. 6.2):

```
app/adapters/
├── __init__.py      # ADAPTER_CLASSES — карта имя → класс (ТЗ п. 6.4)
├── base.py          # SourceAdapter протокол, BaseApiAdapter
├── registry.py      # AdapterRegistry: конфиг из БД, секреты из env
├── encar.py         # EncarApiAdapter (api.encar.com → UnifiedListing)
└── common/
    ├── http.py      # HttpClient: ретраи, 429 backoff, ротация прокси
    ├── proxy.py     # ProxyPool (Redis: proxy:good/bad/cooldown)
    ├── normalize.py # нормализация марок/топлива/кузова/КПП/привода
    └── dedup.py     # кросс-дедуп по VIN/fuzzy, TTL 48ч
```

Запуск Fetch Worker:

```bash
uv run python -m app.workers.fetch
# или через compose: docker compose up worker_fetch
```

Регистрация источника (валидация adapter_class — ТЗ п. 6.4):

```bash
curl -X PUT http://localhost:8000/api/admin/sources/encar \
  -H "X-Internal-Token: $APP_SECRET_KEY" -H "Content-Type: application/json" \
  -d '{
    "name": "encar", "adapter_class": "EncarApiAdapter", "kind": "api",
    "enabled": true, "priority": 100, "rate_limit_per_min": 60,
    "max_concurrency": 3, "timeout_sec": 30,
    "capabilities": {"country": "KR", "price_currency": "KRW"},
    "config": {"page_size": 20}
  }'
```

Запуск парсинга вручную (по расписанию — Scheduler на этапе 8):

```bash
curl -X POST "http://localhost:8000/api/admin/sources/encar/fetch?max_pages=1" \
  -H "X-Internal-Token: $APP_SECRET_KEY"
```

Тесты: `uv run pytest` (контрактные тесты адаптера — мок ответа → `UnifiedListing`, ТЗ п. 14.3 этап 2).

## RAG-микросервис (services/rag)

Локальный RAG-сервис на ядре [rag_service](https://github.com/Pavel14701/rag_service) (monorepo-версия):
гибридный поиск (vector + BM25/RRF), агентный retrieval, multi-provider LLM, DLQ/retry через RabbitMQ.

Структура `services/rag/`:
- `rag_core/` — общее ядро (domain / application / infrastructure, hexagonal);
- `rag_worker/` — RabbitMQ-консьюмеры (`query_queue`, `ingest_queue`, `delete_queue`, `reindex_queue`), health/metrics на :8101;
- `rag_api/` — HTTP-шлюз `POST /api/v1/ask` (internal token → JWT → RPC в `query_queue`), :8100;
- `kb/` — база знаний (markdown: FAQ, таможня, доставка, риски, расходы);
- `scripts/ingest_kb.py` — заливка kb в MinIO/Postgres/очередь индексации.

Инфраструктура в общем docker-compose: `qdrant`, `minio`, `rag_migrate*`, `rag_worker`, `rag_api`.
Бэкенд ходит в шлюз через `RAG_SERVICE_URL` + `RAG_API_TOKEN` (клиент `app/ai/rag_client.py` уже готов;
ниже порога `RAG_CONFIDENCE_THRESHOLD` — fallback на LLM-агента).

Запуск и заливка базы знаний:
```bash
docker compose up -d qdrant minio rag_worker rag_api
docker compose exec rag_worker alembic upgrade head   # если rag_migrate_alembic не отработал
uv run --project services/rag python scripts/ingest_kb.py --force
```
