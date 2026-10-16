
# Auto Selection — PROGRESS

## 32. raw_payload → S3/MinIO (фаза 6)

- **boto3 vs aioboto3 решено: aioboto3** — fetch/send воркеры асинхронные,
  синхронный boto3 блокировал бы event loop. **Миграция `0012_raw_payload_s3`**:
  колонка `listings.raw_payload` (JSONB, раздувала БД/бэкапы) выпилена, вместо
  неё `raw_key` (TEXT) — ключ S3-объекта; NULL = не загружен (MinIO лежал /
  флаг выключен).
- **`S3Storage`** (`app/services/storage.py`): ленивый импорт aioboto3,
  botocore `Config` (retries=3, connect 5s / read 30s), MinIO через
  `endpoint_url`; ключ `raw/{source}/{yyyy}/{mm}/{listing_id}_{content_hash}.json`
  (retention по префиксу + бесплатная дедупликация: хэш не менялся — объект
  не перезаписываем); S3 Metadata (source/listing_id/content_hash/fetched_at);
  `put_raw`/`presign_raw`/`delete_older_than`/`ensure_bucket` — все с
  fallback: ошибка → warning в лог, объявления сохраняются.
- **`save_listings(..., storage=)`**: NEW/смена `content_hash` → параллельная
  загрузка (`asyncio.gather`, `return_exceptions`) + executemany
  `UPDATE listings SET raw_key`; без изменений → загрузки нет.
  Fetch-воркер создаёт storage в startup-ctx (`ensure_bucket`).
- **Retention-крон** `raw_retention_job` 03:30 UTC: list_objects_v2 пагинацией
  → пакетный delete_objects (≤1000), объекты старше `RAW_RETENTION_DAYS=180`.
- **Админ-endpoint `GET /api/admin/listings/{id}/raw`** (internal token):
  presigned GET на 10 мин (`RAW_PRESIGN_TTL_SEC`), прямого доступа к MinIO
  у админки нет; raw_key NULL → 404, storage недоступен → 502.
- Конфиг: `RAW_PAYLOAD_ENABLED` (по умолчанию false), `S3_ENDPOINT_URL`/
  `S3_BUCKET`/`S3_ACCESS_KEY`/`S3_SECRET_KEY`/`S3_REGION` (через `env_file: .env`
  в compose; в docker-сети endpoint `http://minio:9000`). PLAN: открытый
  вопрос закрыт; фаза 7 — добавлен план (RANGE по `recorded_at`, PK обязан
  включать ключ, pg_partman, связка «горячее/холодное» с MinIO).
- **Edge-кейсы (rev. 2)**: retention-крон возвращает ключи удалённых объектов
  и обнуляет `listings.raw_key` (`UPDATE ... WHERE raw_key = ANY(:keys)`,
  чанки по 1000) — иначе через 180 дней админка генерит presigned URL в
  никуда. `ensure_bucket` различает `AccessDenied` (токен без CreateBucket →
  warning «создайте вручную», не stack trace). Гонка retention × fetch
  проанализирована и исключена: raw_key ставится только для NEW/смены хэша,
  ключ содержит yyyy/mm парсинга — совпасть с удаляемым (≥180 дней) не может;
  обнуление только по точному совпадению ключа. `downgrade()` 0012 возвращает
  только схему, данные — нет (зафиксировано в миграции).
- Тесты: **314 passed** (+14: формат ключа, загрузка NEW/raw_key-UPDATE,
  unchanged без загрузки, fallback при ошибке S3, disabled, retention
  (удаление + обнуление raw_key, no-op без БД), ensure_bucket
  (создание / AccessDenied), admin presigned URL / 404), ruff чист,
  `alembic heads` → `0012`, `docker compose config` ОК.
- **⏳ Статус: код готов, e2e с живым MinIO НЕ проверен** (главный техдолг
  фазы): реальный fetch → объект по ключу → presigned URL открывается →
  повторный fetch не перезаписывает → retention с RAW_RETENTION_DAYS=0/1
  удаляет и обнуляет raw_key → сценарий с токеном без CreateBucket.
  Проверить на копии БД: `alembic downgrade -1` → `upgrade head`.

## 33. Подготовка `listing_price_history` к партиционированию (фаза 7, без включения)

- Партиционирование в PG не выключается флагом: `PARTITION BY RANGE` — сразу
  партиционированная таблица. Поэтому схема готовится заранее (вариант «B»),
  включение — строго по триггеру (1–2 млн строк).
- **Миграция `0013_price_history_partition_ready`**: PK `(id)` →
  `(id, recorded_at)` (ключ RANGE обязан входить в PK; `id` оставлен как
  tiebreaker `ORDER BY recorded_at, id`) + `recorded_at` NOT NULL. Данные не
  переносятся, таблица не партиционируется. Индекс
  `idx_price_history_listing (listing_id, recorded_at)` уже содержал ключ.
- **Аудит запросов** (PLAN §6): favorites first_price / listings график —
  полная история по `listing_id` (границы по `recorded_at` не добавлены —
  сломали бы семантику; при триггере — опциональный `since`), send
  `_old_price` — `LIMIT 2` по индексу. Все запросы — через
  `idx(listing_id, recorded_at)` → per-partition index scans.
- ORM: `ListingPriceHistory.recorded_at` — `primary_key=True, nullable=False`
  (композит с `id`); вставка идёт raw SQL — поведение не меняется.
- Тесты: 314 passed, `alembic heads` → `0013`.

## 31. Перевод тайтлов: NLLB-микросервис (фаза 5)

- **Размещение NLLB (открытый вопрос PLAN) решено**: отдельный optional-сервис
  `services/translate` (FastAPI + `nllb-200-distilled-600M`, CPU-образ, порт
  8200), не в RAG: изоляция отказа (перевод некритичен) и памяти
  (~2 ГБ RSS), независимый деплой/своп модели. Bearer `API_TOKEN` (опционально),
  `GET /health`, warmup при старте, детект языка по письменности
  (`kor_Hang`/`zho_Hans`/`rus_Cyrl`/`eng_Latn`) для `source_lang=auto`,
  in-process кэш, батчи через transformers pipeline.
- **Миграция `0011_translations`**: `listings.title_raw` (оригинал — до фазы 5
  title вообще не персистился, только content_hash), `title_ru`,
  `source_language`, `translated_at`. Upsert пишет `title_raw`.
- **Триггер перевода — тот же `content_hash`**: `save_listings` возвращает
  `translate_ids` (NEW + смена хэша) → fetch-воркер ставит задачу
  `translate_listings` (та же очередь `fetch`), которая батчами
  (TRANSLATE_BATCH_SIZE) зовёт сервис и пишет `title_ru/source_language/
  translated_at` (executemany). Ошибка сервиса → title_ru остаётся NULL,
  UI показывает оригинал.
- **`TranslateClient`** (`app/services/translate_client.py`): батчевый
  POST /translate, Redis-кэш `tr:{tgt}:{src}:{sha1}` TTL 30 дней,
  retry с backoff (5xx не ретраим), circuit breaker (3 ошибки → пауза 5 мин +
  алерт), `TRANSLATE_ENABLED` + `TRANSLATE_SERVICE_URL` (пусто = выключен),
  `TRANSLATE_SOURCE_LANGS` (JSON: encar/kcar → kor_Hang, che168 → zho_Hans).
- **UI**: `ListingOut` + `title_raw`/`title_ru`; Mini App — подзаголовок
  с переводом на карточке, «Название» на экране объявления; карточка бота
  предпочитает `title_ru`.
- Тесты: **300 passed** (+15: клиент — кэш/batch/fallback/CB, задача
  `translate_listings`, `translate_ids`, карточка с title_ru), ruff чист,
  `alembic heads` → `0011`, `tsc`/`vite build` ОК.

## 30. Отслеживание объявлений: наличие, история цен, статусные уведомления (фазы 1-4)

- **Миграция `0009_listing_tracking`**: `content_hash`, `is_active`,
  `first_seen_at`/`last_seen_at` (бэкфилл из `parsed_at`), `disappeared_at`/
  `price_changed_at`, таблица `listing_price_history`, partial-индекс активных,
  индекс по `last_seen_at`, BRIN по `first_seen_at`, индекс истории цен.
- **Upsert переписан на CTE** (1 запрос): `old_price`/`old_currency`/`old_hash`/
  `was_active` из `existing` (FOR UPDATE) + RETURNING; `first_seen_at` не
  сбрасывается, `disappeared_at = NULL` при реактивации; дедуп: cross-source
  Redis до записи, in-source — `ON CONFLICT` (source, source_id).
- **`compute_content_hash`** = sha256(title|price|currency|mileage|sorted images);
  смена хэша — единый триггер «объявление изменилось» (в фазе 5 — повторный
  перевод тайтла). Точки цены пишутся при NEW и смене пары (price, currency).
- **Фаза 3**: `0010_sent_log_kind` (PK → user_id+listing_id+kind), `mark_stale`
  (CB-aware, RETURNING id), cron `stale_check_job` 04:00 UTC, статусные
  уведомления `listing_status_scan` → `send_listing_status`
  (price_drop/gone/returned; аудитория — получатели из sent_log; rate limit,
  тихие часы, антидубль по kind).
- **Фаза 4**: история цен в UI — `PricePointOut`, в `ListingOut` добавлены
  `is_active`/`first_seen_at`/`first_price`/`price_history`; карточка
  `GET /api/listings/{id}` отдаёт `price_history` (ASC; в ленте — нет, иначе N+1),
  `GET /api/favorites` — `first_price` одним батч-запросом. Mini App: секция
  «История цен» на экране объявления, бейджи «⬇️ −N%» / «🔴 Исчезло» на карточке
  и «⬇️ (было X)» / «🔴» в избранном.
- Тесты: **285 passed** (+1 к 284), ruff чист, `alembic heads` → `0010`.

## 29. OpenAI выпилен + ParserDoctor: триаж причин падения источника

- **LLM только за Китай**: провайдер OpenAI удалён из фабрики
  (`glm | anthropic | ollama | mock`), `OpenAICompatibleClient` →
  `ChatCompletionsClient` (GLM/DeepSeek/Ollama), `openai_api_key` из
  config/.env/.env.example убран, модели `gpt-4o*` → `glm-4.6`,
  неизвестный провайдер → явный `LLMError`. Обновлены README/PROGRESS/тесты.
- **ParserDoctor — триаж** (`classify_page` / `classify_fetch_error`):
  - `banned` — HTTP 401/403/406/429 или 2xx-страница с маркерами антибота
    (`captcha`, `安全验证`, `滑动验证`, `punish`, `waf`, …) или пустышка
    < 300 символов → алерт «🚫 бан/геоблок — ротация прокси», LLM не зовём;
  - `server_down` — 5xx, таймауты, ошибки коннекта (HttpClient заворачивает
    их в `FetchError`, код парсится regex) → алерт «🛑 источник недоступен»;
  - `http_error` — прочие 4xx (404/410: страница списка переехала) →
    подсказка проверить `base_url/list_path`;
  - `markup_change` — страница живая, карточек 0: только тут зовём GLM.
- **Cooldown переставлен**: ключ `doctor:{source}` ставится только перед
  LLM-ремонтом — бан и падение сервера триажатся при каждом запуске врача
  (раньше cooldown 6 ч глотал бы повторные диагнозы).
- Тесты: **264 passed** (+5: 403/5xx/капча-200/c cooldown/unit-классификаторы);
  ruff чист.


## 28. Фаза 5 — deferred [бэкенд] items (UI/UX roadmap wrap-up)

- **Batch-claim**: `POST /api/partner/leads/batch` `{"limit":1..10}` — атомарно
  забирает до N доступных лидов с учётом дневной квоты (исчерпание → 429,
  как у одиночного claim). Сервис `partners.claim_batch`, аналитика
  `lead_sold` c `batch: true`, кнопка «⚡ Взять 3» в пуле ЛК.
- **Биллинг**: каталог тарифов `PLAN_CATALOG` (starter 5/$49, pro 15/$149,
  salon 40/$399), `GET /api/partner/billing` (план, квота, расход), экран
  «Биллинг» в ЛК вместо заглушки (прогресс-бар квоты + таблица тарифов).
- **Тихие часы**: применялись в send worker (фильтр → `NOTIFY_QUIET_HOURS_DEFAULT`);
  добавлены настройка из бота (меню «Уведомления → 🌙 Тихие часы», пресеты
  23:00–08:00 / 22:00–10:00 / выкл через `BackendClient.quiet_hours`) и
  поддержка в `POST /api/notifications/settings` (notify_mode опционален,
  `quiet_hours_clear` для сброса).
- **AI-помощник админа**: `app/services/admin_assistant.py` — tool-loop
  (до 3 итераций) поверх `build_llm_client` (GLM, `LLM_PROVIDER=glm`);
  tools: `leads_stats`, `partners_list`, `sources_health` (только чтение);
  `POST /api/admin/assistant/chat` (X-Internal-Token; LLM недоступен → 503);
  `ChatWidget.tsx` подключён к эндпоинту (история, 503-подсказка про ключ).
- Тесты: **259 passed** (+15: batch-claim сервис/API, биллинг, ассистент);
  ruff чист; фронт `tsc + vite build` — ок.
- Напоминание владельцу: `alembic upgrade head` (0004–0008) и вписать
  `GLM_API_KEY` в `.env` (иначе ассистент отвечает 503 с подсказкой).

> Экосистема подбора автомобилей из-за рубежа (Корея, Китай, ОАЭ и др.) для
> пользователей из РБ / РФ / СНГ: Telegram-бот + WebApp (Mini App) + сайт
> с AI-диалогом, парсингом источников, RAG-базой знаний и монетизацией.
> Реализация ТЗ (`TZ.md`). Обновлено: 11.10.2026 · **208 тестов backend** ·
> дерево чистое.

**Легенда статусов:**

| Метка | Значение |
|---|---|
| ✅ | Готово, покрыто тестами, в кодовой базе |
| ⏳ | Код готов — **ждёт действия владельца** (миграция, creds, живая проверка) |
| 📋 | Бэклог, не начато |

---

## 0. Статус-дашборд

| # | Блок | Статус | Что осталось |
|---|---|---|---|
| 1 | Этапы ТЗ 0–7: бот, backend, AI-агент, RAG, Mini App | ✅ | — |
| 2 | Этап 8: мониторинг, алерты, `/health/detailed`, `/metrics` | ✅ | — |
| 3 | Планировщик: `schedule_cron` + интервальные задачи | ✅ | — |
| 4 | Продуктовая аналитика (P1-7): `analytics_events`, хуки | ✅ | — |
| 5 | Лиды: `POST /api/leads`, `PATCH /api/leads/{id}` | ✅ | — |
| 6 | Адаптер Che168 (P2-10) | ⏳ | `alembic upgrade head`, сверка живого HTML/гео, `enabled=true` |
| 7 | ParserDoctor (ИИ-ремонтник селекторов) | ⏳ | код готов, `doctor_mode=off`; включается в БД |
| 8 | Прокси-шлюз DataImpulse (антибан) | ✅ | включается переменными `.env` при банах |
| 9 | Миграции `0004`–`0006` (аналитика, che168, партнёры) | ⏳ | `alembic upgrade head` |
| 10 | Telegram Stars монетизация | ⏳ | код-путь полный; живой тестовый платёж |
| 11 | Живой E2E RAG-стека (GLM + ingest + запрос) | ⏳ | creds + `ingest_kb.py --force` |
| 12 | Token Bucket Telegram ~30 msg/s (P2-9) | 📋 | кандидат №1 следующего спринта |
| 13 | SQL-дашборды воронок (retention D1/D7, конверсии) | 📋 | кандидат №2 |
| 14 | LLM cost tracking (P3) | 📋 | — |
| 15 | Ротация PQ-ключей (P3) | 📋 | — |
| 16 | Push `refactor/monorepo` (rag_service) | 📋 | ветка готова локально |
| 17 | HTTPS `MINIAPP_URL` для прода | 📋 | домен/TLS |
| 18 | Рефакторинг веб-авторизации (nullable telegram_id) | 📋 | порог ~30k веб-юзеров |
| 19 | Партнёрский контур (P2-11): API ключей, claim лидов, CPL | ✅ | накатить `0006`, создать первого партнёра, веб-ЛК |

### Действия владельца (единственное, что мешает «зелёному» статусу)

- [ ] `uv run alembic upgrade head` — накатить `0004` + `0005` (пп. 6, 9).
- [ ] Che168: сверить селекторы с живым HTML и гео-доступность (403 из РБ →
      включить DataImpulse с `PROXY_DATAIMPULSE_GEO=cn`), затем
      `UPDATE sources SET enabled=true WHERE name='che168'` (п. 6).
- [ ] Опционально: `"doctor_mode": "suggest"` в `sources.config` che168 (п. 7).
- [ ] Партнёрский контур: после миграции создать партнёра
  `POST /api/admin/partners` — ключ показывается один раз;
  договориться с 2–3 салонами/брокерами о пилоте (п. 19).
- [ ] Telegram Stars: тестовый платёж XTR (п. 10).
- [ ] RAG E2E: `GLM_API_KEY`, PQ-ключи, `ingest_kb.py --force`, контрольный
      запрос к `rag-api` (п. 11).

---

# Часть I — обзор проекта

## 1. Что это и для кого

Пользователь заходит в Telegram-бота или Mini App и на естественном языке
описывает, какой автомобиль хочет (марка/модель, бюджет, год, город).
AI-агент собирает критерии, ищет варианты по агрегаторам (Encar, KCar,
Che168), показывает карточки, отвечает на вопросы о доставке/растаможке через
RAG по базе знаний, считает экономику «под ключ» в USD и подписывает
пользователя на уведомления о новых вариантах. Монетизация — Pro-подписка
через Telegram Stars; лиды из диалога отдаются менеджерам.

## 2. Технологический стек

| Слой | Технологии |
|---|---|
| Backend | Python 3.13, FastAPI, uvicorn, Pydantic v2 / pydantic-settings |
| Бот | aiogram 3 (FSM в Redis), long polling |
| БД | PostgreSQL + SQLAlchemy 2 (async) + Alembic (0001–0005) |
| Очереди | RabbitMQ (aio-pika), producer/TaskWorker/DLQ/cron |
| Кэш/состояние | Redis (FSM, DialogState, кэши, прокси-пул, антидубли, cooldown'ы) |
| Парсеры | httpx + selectolax/lexbor (SSR), общий HttpClient (ретраи, 429, прокси) |
| Frontend | React 18 + Vite + TypeScript, @telegram-apps/telegram-ui, React Router |
| RAG-сервис | FastAPI + RabbitMQ worker, Qdrant (гибрид vector+BM25/RRF), MinIO, multilingual-e5 |
| LLM | GLM (Zhipu, основной), fallback: DeepSeek / Anthropic / Ollama / mock (OpenAI выпилен) |
| Криптография | PyJWT, PBKDF2-SHA256 (пароли сайта), гибрид ML-KEM-768 + X25519 + AES-256-GCM (документы) |
| Инфра | Docker Compose (postgres, redis, rabbitmq, qdrant, minio, backend, bot, workers, miniapp, rag_*) |
| Тесты | pytest + pytest-asyncio (**208 тестов** backend), контрактные тесты адаптеров, mypy, ruff |

## 3. Архитектура

```
Пользователь ── Telegram ──► Бот (aiogram3) ─┐
Пользователь ── WebApp/сайт ─► Backend (FastAPI) ── PostgreSQL
                                  │  ▲
                    AI-агент ─────┤  └─ RAG API (:8100) ─► RabbitMQ ─► RAG Worker (:8101)
                    (tool loop)   │                          │
                                  ▼                          ├─ Qdrant (гибридный поиск)
                  Scheduler (schedule_cron) ─► Fetch Worker ─► адаптеры ─► listings
                                                     │  ▲
                                                     │  └─ ParserDoctor (GLM чинит селекторы)
                                                     └─ ProxyPool / DataImpulse (антибан)
                                  ▲
                          Send Worker ─► уведомления в Telegram
```

Потоки:
- **Диалог**: пользователь → агент (LLM + tools) → поиск/вопрос → ответ;
  заинтересованность → `POST /api/leads` → менеджер.
- **Парсинг**: cron/ручной запуск → очередь fetch → адаптеры
  (Encar API / KCar API / Che168 HTML) → нормализация → кросс-дедуп →
  `listings`; при смене вёрстки — ParserDoctor, при банах — прокси-шлюз.
- **Уведомления**: новые листинги → матчинг активных фильтров → Send Worker →
  instant или digest (тихие часы, антидубли).
- **RAG**: `POST /api/v1/ask` (rag_api) → JWT → RPC `query_queue` → worker →
  гибридный поиск Qdrant + LLM-генерация → ответ с источниками и confidence.
- **Аналитика**: хуки в API/боте → `analytics_events` → SQL-агрегаты,
  `events_24h` в `/metrics`.

## 4. Структура репозитория

```
auto_bot/
├── app/                      # Backend
│   ├── api/                  # HTTP-роутеры (см. §6)
│   ├── ai/                   # агент, LLM-клиенты, RAG-клиент, промпты, tools
│   ├── adapters/             # encar, kcar, che168, registry, прокси, дедуп
│   ├── bot/                  # aiogram: handlers, button_flow, __main__
│   ├── core/                 # config, security (initData/JWT), deps, pq_crypto
│   ├── db/                   # models, session, migrations 0001–0005 (alembic)
│   ├── queue/                # RabbitMQ: producer, TaskWorker, DLQ, cron
│   ├── schemas/              # Pydantic-контракты (UnifiedListing, Lead…)
│   ├── services/             # users, dialog, search, notify, subscription,
│   │                         # fetch, analytics, alerting, source_health,
│   │                         # cron, parser_doctor
│   ├── workers/              # fetch, send
│   └── main.py               # FastAPI app (uvicorn app.main:app)
├── frontend/                 # Mini App + сайт (React/Vite/TS)
├── services/rag/             # RAG-микросервис (rag_core/rag_worker/rag_api + kb/)
├── tests/                    # pytest (208 тестов)
├── docker-compose.yml        # весь стек
├── pyproject.toml            # uv-проект backend
├── TZ.md                     # техническое задание
├── PROGRESS.md               # этот файл
└── .env.example              # шаблон конфигурации
```

## 5. Модели данных (основные)

- **User** — telegram_id (PK), username, tier (free/pro), notification_mode,
  **email (unique)** + password_hash (сайт), синтетические отрицательные
  telegram_id для веб-пользователей (PK/FK сквозной).
- **Filter** — SearchCriteria пользователя (марка/модель/цена/год/источники/город).
- **Listing (UnifiedListing)** — нормализованное объявление: VIN (у Che168
  скрыт → None), источник, цена (валюта источника), год/пробег/объём, страна.
- **Favorite** — избранное (миграция `0002_favorites`).
- **Subscription/Payment** — Pro через Telegram Stars.
- **DialogSession** — архивация DialogState из Redis.
- **Lead** — обращение из диалога: user_id, listing_id?, статус
  new/in_progress/closed/rejected, manager_id (таблица в `0001`).
- **AnalyticsEvent** — сырые события воронки (`0004_analytics_events`).
- **Source (админ)** — реестр источников: adapter_class, kind (api/parser),
  приоритеты, rate limits, `schedule_cron`, capabilities, `enabled`,
  `credentials_ref`; миграция `0005` добавляет `che168` (enabled=false).
- Миграции: `0001_initial`, `0002_favorites`, `0003_web_auth` (email unique),
  `0004_analytics_events`, `0005_add_che168_source`.

---

# Часть II — подсистемы

## 6. HTTP API (FastAPI, prefix `/api`)

| Роутер | Эндпоинты |
|---|---|
| `auth` | `POST /auth/telegram` (initData→JWT), `POST /auth/register`, `POST /auth/login`, `POST /auth/magic-link` |
| `users` | upsert профиля |
| `me` | `GET /me` — профиль + tier |
| `filters` | CRUD: `GET/POST /filters`, `PATCH/DELETE /filters/{id}` |
| `listings` | `GET /listings` (поиск/пагинация), `GET /listings/{id}` |
| `favorites` | `GET/POST/DELETE /favorites…` |
| `leads` | `POST /leads` (internal), `PATCH /leads/{id}` (internal: статус, manager) |
| `payments` | `POST /payments/stars` (Telegram Stars), `POST /payments/success` |
| `admin` | `GET/PUT /admin/sources…`, `POST /admin/sources/{name}/fetch`, health источников (X-Internal-Token) |
| `dialog` | `POST /dialog/message`, `POST /dialog/reset` (+ хуки аналитики) |
| `ai_search` | `POST /ai/message`, `POST /ai/reset`, `POST /ai/finalize` (JWT) |
| `notifications` | `POST /notifications/settings` (instant/digest/off), `POST /notifications/scan` |
| system | `GET /health`, `GET /health/detailed` (postgres/redis/rabbitmq, 503 при недоступной БД), `GET /metrics` (агрегаты + `events_24h`; **X-Internal-Token**) |

## 7. Telegram-бот ✅

- Команды (`set_my_commands`, личные чаты): `/start`, `/menu`, `/reset`,
  `/help`, `/stop`.
- **Кнопка-меню с WebApp**: «🚗 Поиск авто» открывает Mini App (`MINIAPP_URL`,
  HTTPS в проде).
- Inline-меню `/menu`: AI-подбор, «📱 Открыть приложение» (WebAppInfo),
  уведомления (мгновенно/дайджест/выкл), подписка, сброс диалога.
- FSM в Redis (`fsm:{user_id}`), кнопочный fallback (`button_flow.py`) при
  недоступности LLM.
- Уведомления: instant (rate-limit) или digest (3–5 карточек, тихие часы,
  антидубли `notified:{uid}:{lid}` TTL 7д).

## 8. AI-агент ✅

- LLM-клиенты (`app/ai/llm_client.py`): `OpenAICompatibleClient` (GLM —
  основной, DeepSeek, OpenAI, Ollama), `AnthropicClient`, `MockLLMClient`
  (тесты/dev), фабрика по `LLM_PROVIDER`.
- Tool loop с подтверждением фильтров; лимит шагов, ранний выход при
  заполненных критичных полях, история 8 сообщений; `DialogState` в Redis
  (`dialog:{user_id}`, TTL 24ч), архивация в `dialog_sessions`.
- Финализация = фильтр в БД (для уведомлений) + задачи парсинга по источникам.
- RAG (`app/ai/rag_client.py`): таймаут 5с, кэш Redis 1ч, circuit breaker
  (5 ошибок → пауза 5 мин), confidence ≥ 0.6 → ответ из БЗ, иначе fallback
  на LLM, затем честное «уточните у менеджера». Промпт-защита от инъекций.

## 9. RAG-микросервис (services/rag) ✅

- `rag_core/` — hexagonal-ядро: domain / application / infrastructure.
- `rag_worker/` — консьюмеры RabbitMQ: `query_queue`, `ingest_queue`,
  `delete_queue`, `reindex_queue`; health/metrics :8101; DLQ/retry.
- `rag_api/` — HTTP-шлюз `POST /api/v1/ask` (internal token → JWT → RPC), :8100.
- Поиск: гибрид vector (multilingual-e5) + BM25/RRF в Qdrant; embedding-кэши;
  опционально query rewriting, semantic cache, parent-child chunking.
- LLM-слой: мультипровайдерный роутер (GLM основной), кэш ответов,
  circuit breaker, лимит `max_tokens`, per-request routing с allowlist.
- Инфра: Qdrant, MinIO (документы), Postgres (реестр документов), Alembic.
- Заливка БЗ: `scripts/ingest_kb.py --force` (поддерживает PQ-шифрование).

## 10. База знаний (services/rag/kb) — РБ / РФ / СНГ + источники ✅

| Файл | Содержание |
|---|---|
| `customs.md` | Единые ставки пошлин ЕАЭС по возрастам (Решение ЕЭК № 107, прил. 6), коммерческий ввоз, РБ (утильсбор, ГАИ), КЗ/Армения/Кыргызстан, порядок действий |
| `fees.md` | Утильсбор РФ (ПП 1291), платежи РБ, СБКТС/ЭПТС, ОСАГО, налоги; **правила конверсии KRW/CNY/EUR/BYN/RUB → USD** (запас ±3–5%) |
| `countries.md` | Источники: Корея (аукционы, Ro-Ro Пусан→Владивосток), Китай (NEV, лицензированный экспортёр, Хоргос/Маньчжурия), ОАЭ, Европа/США, Япония; методика расчёта «под ключ» |
| `delivery.md` | Таблица маршрутов/сроков, что входит во фрахт, фотофиксация, трекинг |
| `risks.md` | Юридические, технические, финансовые риски; чек-лист сделки |
| `legal.md` | Правовая база РФ/РБ/ЕАЭС, экспортные ограничения стран-источников, compliance: обход санкций не поддерживается |
| `faq.md` | FAQ: регистрация, валюты, растаможка по возрастам, уведомления, документы |

Все ставки помечены «проверять на дату ввоза» — зафиксированы только
устойчивые нормы, летучие цифры — с явными оговорками.

## 11. Frontend (Mini App + сайт) ✅

Дополнительно (фазы 1–4 UI/UX ТЗ): полировка Mini App (фото, skeleton, тосты,
динамические чипсы, бесконечный скролл, галерея, онбординг 3 шага + миграция
0007 `users.onboarded_at`), партнёрский ЛК `/partner/*`, админка `/admin/*`
(источники/партнёры/лиды, вход по internal-токену), audit log (миграция 0008
`admin_actions`, логируются create/rotate партнёра), скелет AI-виджета в админке.

UI/UX-ТЗ для всех поверхностей: `docs/UIUX_TZ.md` (v1.0 — дизайн-токены,
Mini App, админка, партнёрский ЛК, бот, тон голоса, чеклист внедрения).

- Экраны: `Onboarding`, `Feed` (лента с чипсами-фильтрами), `Filters`,
  `Listing`, `Favorites`, `Profile`, `AiSearch`, `Login` (email+пароль).
- `api/`: `client.ts` (JWT, base `/api`), `auth.ts`, `ai.ts`, `types.ts`.
- Telegram-интеграция: `telegram-web-app.js`, initData → JWT, тема
  `--tg-theme-*`; в проде — HTTPS `MINIAPP_URL`.
- Вне Telegram — read-only лента + Login для полного доступа.

## 12. Безопасность ✅

- **Telegram initData**: HMAC-SHA256-валидация, max age 24ч.
- **JWT**: access 1ч + refresh 30д (`APP_SECRET_KEY`, HS256).
- **Пароли сайта**: PBKDF2-SHA256, 240k итераций; email — regex-валидация.
- **Веб-пользователи**: синтетические отрицательные telegram_id (PK/FK),
  ретрай 5× при коллизии (долг: отдельный PK, см. бэклог).
- **Внутренние эндпоинты**: `X-Internal-Token` (`INTERNAL_TOKEN`; пустое —
  обратная совместимость с `APP_SECRET_KEY`).
- **RAG**: internal token backend↔rag_api, JWT rag_api↔worker, ротация
  ключей HS/RS/ES, PII-редакция перед записью диалогов.
- **Постквантовое шифрование документов**: гибрид ML-KEM-768 (FIPS 203) +
  X25519 → HKDF-SHA256 → AES-256-GCM, контейнер `AUPQ1`, AAD = ключ объекта.
  `EncryptedFileStorage` над MinIO; включение `ENCRYPT_DOCUMENTS=true` +
  PQ-ключи. Устойчиво к «harvest now, decrypt later».

## 13. Конфигурация (.env)

Основные переменные (полный список — `.env.example`):
`APP_ENV`, `APP_SECRET_KEY`, `INTERNAL_TOKEN`, `APP_BASE_URL`, `MINIAPP_URL`,
`BOT_TOKEN`, `ADMIN_CHAT_ID`, `MANAGER_CHAT_ID`, `DATABASE_URL`, `REDIS_URL`,
`RABBITMQ_URL`; LLM: `LLM_PROVIDER=glm`, `GLM_API_KEY`, `GLM_BASE_URL`,
`LLM_DEFAULT_MODEL=glm-4.6`; RAG: `RAG_SERVICE_URL`, `RAG_API_TOKEN`,
`RAG_JWT_SECRET`, `RAG_CONFIDENCE_THRESHOLD`; алерты: `ALERTS_ENABLED`,
`DLQ_ALERT_THRESHOLD`, `SOURCE_CB_ERROR_STREAK`; PQ: `ENCRYPT_DOCUMENTS`,
`PQ_ENCRYPTION_PUBLIC_KEY/PRIVATE_KEY`; прокси: `PROXY_DATAIMPULSE_USER`,
`PROXY_DATAIMPULSE_PASS`, `PROXY_DATAIMPULSE_GEO`, `PROXY_DATAIMPULSE_PORT`
(823 HTTPS / 824 SOCKS5), `PROXY_DATAIMPULSE_STICKY_TTL_SEC`.

## 14. Docker Compose — сервисы

`postgres`, `redis`, `rabbitmq`, `qdrant`, `minio`, `rag_migrate` (Alembic
rag), `rag_worker` (:8101), `rag_api` (:8100), backend, bot, `worker_fetch`,
`worker_send`, `miniapp` (:8080). Тома: pg_data, redis_data, qdrant_storage,
minio_data.

## 15. Запуск и деплой

Локально:
```bash
uv sync && cp .env.example .env   # заполнить BOT_TOKEN, APP_SECRET_KEY, GLM_API_KEY
docker compose up -d postgres redis rabbitmq
uv run alembic upgrade head
uv run uvicorn app.main:app --reload        # :8000
uv run python -m app.bot                    # бот (команды + меню-кнопка)
uv run python -m app.workers.fetch          # cron-планировщик + парсинг
uv run python -m app.workers.send
cd frontend && npm install && npm run dev   # :8080
```

RAG-стек:
```bash
docker compose up -d qdrant minio rag_worker rag_api
uv run --project services/rag python scripts/ingest_kb.py --force
```

Прод-деплой: `docker compose build` → `up -d` → `alembic upgrade head`
(основная + rag БД) → ingest KB → секреты (GLM_API_KEY, RAG_API_TOKEN,
RAG_JWT_SECRET, PQ-ключи) → HTTPS для MINIAPP_URL.

---

# Часть III — журнал реализации (что наворотили)

Все крупные изменения спринтов «ревью → сейчас». Метки: ✅ закрыто и
протестировано; ⏳ код готов, ждёт владельца.

## 16. ✅ Этап 8 — мониторинг и алерты

- `app/services/alerting.py`: алерты в Telegram на `ADMIN_CHAT_ID`, дедуп
  Redis `alert:{key}` с **эскалацией** (повторы глушатся экспоненциально:
  30 мин → 1 ч → 2 ч, cap 2 ч), `ALERTS_ENABLED`.
- DLQ-монитор в `TaskWorker` (глубина `dead_letter` ≥ `DLQ_ALERT_THRESHOLD` →
  алерт, период `HEALTH_CHECK_INTERVAL_SEC`).
- Алерты на открытие/восстановление circuit breaker'а источников и RAG-CB.
- `GET /health/detailed` (postgres/redis/rabbitmq, 503 при недоступной БД),
  `GET /metrics` (пользователи по tier, объявления, уведомления, latency
  источников, `events_24h`) — закрыт `X-Internal-Token`.
- Попутно исправлен латентный баг: `error_streak`/`total_fetched` в
  `source_health` были `None` до первого flush → `TypeError` на первом
  инциденте нового источника (SQLAlchemy column `default=` срабатывает только
  на flush — инстанцировать с явными значениями).

## 17. ✅ Планировщик (schedule_cron)

- `app/services/cron.py` — матчер 5-полей (*/n, диапазоны, списки; dom+dow —
  OR по стандарту cron).
- `IntervalSpec` в `TaskWorker` (интервальные задачи с Redis-локом
  `interval:{name}:{slot}`).
- Fetch-воркер каждую минуту: `schedule_fetch_due` → источники `enabled` с
  подходящим `schedule_cron` ставятся в очередь (лок
  `sched:{source}:{YYYYMMDDHHMM}` от дублей; CB проверяется внутри задачи).

## 18. ✅ Закрытые пункты ревью (факты по коду)

- Fuzzy-дедуп: `app/adapters/common/dedup.py` — VIN + fuzzy-хэш
  (make|model|year|mileage|price), TTL 48 ч, Redis SET NX.
- Circuit breaker источников: `source_health.py` + проверка в fetch-воркере;
  порог `SOURCE_CB_ERROR_STREAK=5` вынесен в env.
- Callback'и уведомлений бот↔backend совпадают (`instant/digest/off`).
- `INTERNAL_TOKEN` выделен из `APP_SECRET_KEY` (обратная совместимость).
- `Dockerfile`, `alembic.ini`, `frontend/*` закоммичены.

## 19. ✅ Продуктовая аналитика (P1-7)

- Таблица `analytics_events` (миграция `0004`) + `app/services/analytics.py` —
  `track()` по принципу alerting: ошибка записи не ломает бизнес-поток.
- Хуки: `user_registered` (первый upsert, `source: bot|miniapp`),
  `search_completed` (только поиск с фильтрами, не просмотр ленты;
  results/filters), `dialog_message` (`/dialog/message`),
  `subscription_activated` (`/payments/success`, tier/days/stars, только
  первая активация — идемпотентность сохранена).
- `lead_created` → воронка search → dialog → lead замкнута.
- `events_24h` по именам — в `/metrics`. Тесты: `tests/test_analytics.py`.

## 20. ✅ Лиды (воронка диалог → обращение)

- `POST /api/leads` (internal, из бота/Mini App: user_id, listing_id?,
  source?, comment) → событие `lead_created`.
- `PATCH /api/leads/{id}` (internal): статус new/in_progress/closed/rejected
  + manager_id — менеджерский процесс.
- Таблица `leads` была в `0001`. Явный `status="new"` при создании
  (тот же класс бага с column `default=`, что и у `SourceHealth`).
  `Literal`-валидация статуса. Тесты: `tests/test_leads.py`.

### 20.1. ✅ Партнёрский контур (P2-11): CPL-продажа лидов

- `partner_accounts` + `partner_api_keys` (миграция `0006`): план/квота,
  статус; ключ хранится только SHA-256 хэшем, сырой `pi_…` показывается один раз.
- Admin API (`X-Internal-Token`): `POST/GET /api/admin/partners`,
  `POST /{id}/rotate` (мгновенная инвалидация старого ключа), `/{id}/revoke`, `PATCH /{id}`.
- Partner API (`X-API-Key`): `GET /me` (ЛК-состояние), `GET /leads` (пул
  квалифицированных лидов без ПД), `POST /leads/{id}/claim` (эксклюзивный
  захват, 409 если занят, 429 по квоте), `POST /leads/{id}/feedback`
  (accepted/returned/junk/deal — доказательство качества лида). Rate limit
  fixed window в Redis (`PARTNER_RATE_LIMIT_PER_MIN`), fail-open при недоступном Redis.
- События воронки: `partner_lead_claimed`, `partner_lead_feedback`.
- `leads`: `partner_id`, `partner_status`, `claimed_at`, `qualification` (JSONB).
- Тесты: `tests/test_partners.py` (28). Веб-ЛК партнёра — следующий шаг (бэкенд готов).

## 21. ⏳ Адаптер Китая Che168 (P2-10) — код готов, ждёт включения

- `Che168HtmlAdapter` (kind='parser') — SSR-парсинг selectolax/lexbor через
  общий `HttpClient` (ретраи, 429, прокси-ротация, MockTransport-инъекция).
- Нормализация: «23.80万» → 238000 CNY, «5.2万公里» → 52000 км, марка
  иероглифами (丰田 → Toyota) — ~50 CJK-марок в `MAKE_MAP`; топливо/КПП
  поиском подстроки (`detect_fuel`/`detect_transmission` — точный
  `normalize_*` на тексте карточки не работает).
- Селекторы/путь списка — в `sources.config` (смена вёрстки правится в БД
  без деплоя); `raw_payload` хранит HTML карточки; VIN=None (скрыт) → общий
  fuzzy-дедуп. Healthcheck = 200 + контейнер списка (капча/вёрстка → False).
- Миграция `0005` вставляет источник `che168` (cron `*/30 * * * *`,
  **enabled=false**), capabilities содержат `use_proxy=true`.
- Зависимость `selectolax` (в venv 1.0.0 — импорт из `selectolax.lexbor`,
  Modest deprecated). Тесты: `tests/test_che168_adapter.py` (13).
- **Для включения:** `alembic upgrade head` → сверить селекторы с живым HTML
  и гео (403 из РБ → DataImpulse, `PROXY_DATAIMPULSE_GEO=cn`) →
  `UPDATE sources SET enabled=true WHERE name='che168'`.

## 22. ⏳ ParserDoctor — ИИ-ремонтник парсеров

Когда источник отвечает, но парсер отдал 0 карточек (смена вёрстки),
fetch-воркер (fire-and-forget) зовёт `app/services/parser_doctor.py`:
живой HTML → GLM предлагает CSS-селекторы (card/title/price/link) →
**локальная валидация** на том же HTML (≥3 карточек, уникальные ID, цены у
половины+) → режим `doctor_mode` в `sources.config`:

| Режим | Поведение |
|---|---|
| `auto` | применить к БД + `AdapterRegistry.reload_source` + алерт «было/стало» |
| `suggest` | только алерт владельцу с проверенным предложением |
| `off` (default) | выключен |

Cooldown Redis 6 ч (`doctor:{source}`, LLM платный). Любой сбой (LLM
недоступен, мусорный JSON, невалидные селекторы, геоблок) не ломает фетч и
не меняет конфиг. Тесты: `tests/test_parser_doctor.py` (12).
**Включение:** `"doctor_mode": "auto"` (или `suggest`) в `sources.config`.

## 23. ✅ Прокси-шлюз DataImpulse (антибан, опционально)

- Формат по официальным докам: `http://login__cr.cn;sessid.X:pass@gw.dataimpulse.com:823`
  (823 HTTPS / 824 SOCKS5; `cr.*` — страна выхода, `sessid.*` — sticky ~30 мин).
- `GatewayProxyPool` (`app/adapters/common/proxy.py`): ротацию IP делает
  провайдер (per request или детерминированное sessid-окно
  `PROXY_DATAIMPULSE_STICKY_TTL_SEC`, 0 = ротация на каждый запрос);
  `mark_bad` не выбрасывает шлюз навсегда — N ошибок соединения подряд
  усыпляют его на cooldown, `get()` возвращает None → HttpClient ходит
  напрямую (обрыв шлюза не убивает парсинг).
- Фабрика `build_proxy_pool(settings, redis)`: user+pass заданы → шлюз,
  иначе статический `ProxyPool` из Redis.
- Подключается в `AdapterRegistry` только к источникам с
  `capabilities.use_proxy=true` (che168 — да, encar API — нет: платный
  трафик не жжётся).
- **Включение:** `PROXY_DATAIMPULSE_USER` + `PROXY_DATAIMPULSE_PASS`
  (+ `PROXY_DATAIMPULSE_GEO=cn` при геоблоке) в `.env`, рестарт воркера.
  Тесты: `tests/test_proxy.py` (11) + 2 в `tests/test_registry.py`.

---

# Часть IV — долги и планы

## 24. ⏳ Ожидает действий владельца

| # | Действие | Зачем |
|---|---|---|
| 1 | `alembic upgrade head` (0004+0005) | аналитика + источник che168 |
| 2 | Сверка che168 с живым HTML + гео | перед `enabled=true`; 403 → DataImpulse `GEO=cn` |
| 3 | Stars: тестовый платёж XTR | код-путь готов, живой оплаты не было |
| 4 | RAG E2E: GLM/PQ-ключи, `ingest_kb.py --force`, контрольный запрос | живой контур ответов |
| 5 | Опционально: `doctor_mode=suggest` | наблюдать за предложениями «врача» без авто-применения |
| 6 | `alembic upgrade head` (+0006) и создание первого партнёра | партнёрский контур: ключ один раз через admin API |

Уже сделано владельцем: **BOT_TOKEN отозван** через @BotFather (перевыпустить
и обновить `.env` перед продом).

## 25. 📋 Бэклог (приоритизирован)

### P2 — кандидаты следующего спринта

1. **Token Bucket Telegram** ✅ реализован (п. 12.6 ТЗ): Redis-бакеты
   (Lua, атомарно) ~30 msg/s глобально + ~1 msg/s на чат, hook в
   `send_telegram_message`; fail-open при недоступном Redis (429 —
   страховка); `TELEGRAM_RATE_*_PER_SEC` (0 = выкл).
2. **SQL-дашборды воронок** ✅ реализованы: `app/services/funnels.py` +
   `GET /api/admin/analytics/funnel` (search→dialog→lead→subscription,
   conv_prev/conv_first %) и `GET /api/admin/analytics/retention`
   (когорты по дню `user_registered`, D1/D7).
3. **Веб-ЛК партнёра** ✅ реализован (`/partner/*` во фронтенде): вход по
   ключу, дашборд, пул + claim (409/429), мои лиды + фидбек, API-ключ;
   ротация ключа — через менеджера/админку. Бэкенд: `?mine=1` в `/api/partner/leads`.
4. **Биллинг партнёров** ✅ реализован (миграция `0014`): CPL-списание
   при claim (индивидуальный `cpl_rate` или тариф плана
   `PARTNER_CPL_RATES`), журнал `partner_transactions` (+topup /
   −charge_lead, balance_after), при исчерпании — suspended + алерт
   (дедуп 24ч), пополнение `POST /api/admin/partners/{id}/billing`
   снимает suspended; claim без денег → 402. Баланс и цена лида — в
   `/api/partner/me` и `/api/partner/billing`.

### P3 — стратегическое

3. LLM cost tracking: токены/стоимость GLM в `DialogSession` или отдельной
   таблице, сводка в `/metrics` (unit-экономика).
4. Ротация PQ-ключей и разделение доступа к приватному ключу.
5. Push ветки `refactor/monorepo` (rag_service: uv-workspace, 361 тест —
   ветка готова локально).
6. Публичный HTTPS `MINIAPP_URL` для прода + проверка кнопки меню.
7. Рефакторинг веб-авторизации: `telegram_id` → nullable, отдельный PK
   (синтетические отрицательные id — коллизии после ~30k веб-юзеров).

## 26. Тесты и качество

- `uv run pytest` → **504 passed** (vitest: +9): LLM-клиенты, RAG-клиент (fallback, CB,
  кэш), агент, уведомления, адаптеры (контракты, дедуп, che168), auth,
  PQ-крипто, мониторинг/алерты, cron/планировщик, аналитика, лиды,
  ParserDoctor, прокси (GatewayProxyPool, фабрика), партнёрский контур
  (ключи/ротация, эксклюзивный claim, квоты, rate limit, фидбек),
  S3/MinIO storage, token bucket, воронки (funnel/retention),
  партнёрский биллинг (CPL-списания, topup, suspended/reactivate).
- `ruff check` — чисто; mypy настроен. Frontend: `tsc --noEmit` + `vite build`.
- Тест-хелперы: `FakeSession` (строковые PK, `rollback()`, `get`),
  `FakeRedis` (`ttls`), `MockLLMClient` (`script`, `.calls`).

## 27. История коммитов (последние)

| Commit | Что вошло |
|---|---|
| `1c898e4` | ТЗ (TZ.md) |
| `b448c76` | RAG-микросервис, /api/ai, AI-поиск в Mini App, inline-меню, compose |
| `a1d39c7` | Регистрация/логин сайта, Login-экран, миграция 0003 |
| `46a7eb4` | GLM основной, БЗ РБ/РФ/СНГ, TG-кнопка WebApp, PQ-шифрование |
| `7489aec`, `7a29eb3` | Аналитика (P1-7) + лиды: хуки, `POST/PATCH /api/leads`, `events_24h` |
| `3dbe08e` | Che168-адаптер (P2-10): парсер, нормализация 万, миграция 0005 |
| `6a3f934` | ParserDoctor: GLM-ремонт селекторов, doctor_mode, reload_source |
| `ffb50dd` | Прокси-шлюз DataImpulse (GatewayProxyPool, use_proxy) |
| `ffe4a0e` | Партнёрский контур (P2-11): ключи с ротацией, claim лидов, квоты, rate limit |
| `8dc790f` | Token Bucket Telegram (P2-12): 30 msg/s глобально + 1 msg/s на чат, Redis/Lua |
| `c987714` | SQL-дашборды воронок (P2-13): funnel + retention D1/D7 admin-эндпоинты |
| (см. git) | Биллинг партнёров (P2-14): CPL-списания, баланс, suspended/reactivate, миграция 0014 |
| (отд. репо) | rag_service monorepo: 361 тест, ветка `refactor/monorepo` (не запушена) |
| `15400e9` | Encar: новый API-контракт (sr/inav/cursor, q с `_.`, корейские фасеты, FormYear/Year, USD→KRW) |
| `10dcceb` (PR #1) | Микросервисы: gateway + 6 доменных сервисов (профили mono/micro), auth refresh, багфиксы (`max→le`, `user.telegram_id`, html.escape) |
| (этот merge) | Мерж PR #1 + правки: env-оверрайды DATABASE_URL/REDIS_URL/RABBITMQ у 6 svc, `depends_on required:false` (bot/miniapp в micro), mvp.sh `--profile mono`, `/metrics` вынесен в `app/api/metrics.py` (admin + gateway-прокси), ParserDoctor: инъекция redis в send_alert + изоляция Telegram в тестах (442 теста, ruff clean) |
| (этот коммит) | KCar: живой API (`/bc/search/list`, AES-enc из бандла фронта, wr_-фильтры, top-N) + персистентность доктора: admin PUT сливает config/capabilities/retry_policy, mvp.sh создаёт источник только при отсутствии; MAKE_MAP + корейские марки (447 тестов, ruff clean) |

## 28. Ревью PR #1 (feat/microservices) — найденные и исправленные проблемы

1. **compose**: у 6 доменных сервисов (identity/catalog/billing/partners/dialog/admin)
   не было `environment:`-оверрайдов `DATABASE_URL/REDIS_URL/RABBITMQ_URL` — при
   `localhost` в `.env` они не связывались бы с инфраструктурой (тот же баг, что
   ранее исправлен для backend/workers).
2. **compose**: `bot` и `miniapp` зависели от `backend` (профиль mono) — в профиле
   `micro` не стартовали; заменено на `depends_on ... required: false` для
   backend+gateway (Compose ≥2.20).
3. **mvp.sh**: переведён на `--profile mono` (иначе сервисы с профилями не поднимутся).
4. **`GET /metrics`** (ТЗ п.10) жил только в `app/main.py` — в режиме micro gateway
   (только `/api/*`) отдавал 404: роут вынесен в `app/api/metrics.py`, подключён
   монолитом и admin-сервисом, gateway проксирует `/metrics` → admin.
5. **ParserDoctor**: `send_alert` ходил в реальный Redis через `_get_redis()` —
   тесты падали при остановленном Docker (9 падений); добавлена инъекция
   `redis=` в `send_alert`, проброс через `_repair`/`_triage_result`, в тестах
   изолирован реальный Telegram (`_send_telegram` → stub).

Конфликт мержа (docker-compose.yml) разрулен: профили PR + env-оверрайды локальных
коммитов сохранены.

## 29. KCar: живой API + фикс персистентности ParserDoctor

1. **kcar.com — источник снова рабочий.** Старый контракт
   (`api.kcar.com/dst/...`, GET `?page=&count=`) мёртв (404). Восстановлен
   актуальный: `POST https://api.kcar.com/bc/search/list`, тело
   `{"enc": base64(AES-CBC(JSON))}` — ключ `SKFJ2424DasfaJRI`, iv
   `sfq241sf3dscs321` (воспроизведено из фронтового бандла `_nuxt/*.js`,
   `setEncDef`), PKCS7. Ответ `{"data": {"totalCnt", "rows": [...]}}`.
   Пагинации в API нет (page/pageNo/... игнорируются — проверено запросами):
   адаптер берёт top-N (`limit`, по умолчанию 100) свежих объявлений,
   `has_more=False`. Фильтры `wr_*` (make/model/годы YYYYMM/цена в манах/
   пробег/топливо — код «001»=가솔린 подтверждён живым ответом; остальные
   коды не шлём). Карта полей: carCd/prc(ман)/milg/prdcnYr/mnuftrNm/
   modelNm/grdNm+grdDtlNm/carWhlNm/fuelNm/trnsmsnNm/cntrRgnNm/lsizeImgPath;
   карточка: `/bc/detail/CarInfoDtl?carCd=<carCd>`. Live-check: 100 items,
   Hyundai с 2018 → total 7480.
   `HttpClient` получил `post/post_json` (общий `_request` с ретраями/429).
2. **Персистентность ParserDoctor.** Корень бага «ремонт слетает после
   рестарта»: `PUT /api/admin/sources/{name}` замещал `config` целиком, а
   `mvp.sh up --parse` при каждом старте перезаписывал конфиг
   `{"page_size": 20}`. Исправлено дважды:
   - admin PUT теперь СЛИВАЕТ словари `config/capabilities/retry_policy`
     (ключи из body перекрывают, остальные сохраняются); скалярные поля —
     как раньше;
   - `mvp.sh` создаёт источник только если его ещё нет (GET `/sources`
     → PUT при 404), существующий конфиг не трогает.
   Регресс-тесты: `test_upsert_source_merges_config`,
   `test_upsert_source_new_creates_with_body_config`.
3. **normalize**: MAKE_MAP дополнен корейскими именами марок из живых API
   (벤츠→Mercedes-Benz, 포르쉐→Porsche и т.д. — иначе kcar/encar значения
   не нормализовались).
4. **che168** (вне этого коммита): desktop за Ali WAF + EdgeOne captcha;
   обход уровня 1 (acw_tc + solve-куки, Baidu-referer) даёт живой HTML
   нестабильно (вероятностный WAF), уровень 2 — капча. Нужны прокси
   (DataImpulse creds пустые) или headless-браузер; адаптер триажит
   капчу как ban-исход.

**447 тестов зелёные, ruff clean.**

### 30. E2E-валидация персистентности + живой синк kcar в стеке (коммит «fix: e2e hardening…»)
1. **E2E e2e-проверка фикса персистентности пройдена**: сломанный→«отремонтированный»
   конфиг encar (`doctor_note` в config) + включённый kcar пережили
   рестарт `mvp.sh up --parse` — ни PUT-reseeding, ни перезапись конфига.
2. **Фикс найден по ходу e2e**: строки источников с NULL `retry_policy`/
   `capabilities`/`config` в БД роняли `GET /api/admin/sources` целиком
   (500, ValidationError в SourceConfig) — добавлен `field_validator`,
   None → дефолты. Тест `test_source_config_null_db_fields_to_defaults`.
3. **`HttpClient._request`: таймауты теперь ретраятся** — kcar отдаёт тело
   списка медленно (httpx.ReadTimeout после 200), раньше не ловился вовсе.
   Добавлены `httpx.TimeoutException` в retryable с backoff 2^attempt.
   Тесты: `tests/test_http_client.py` (успех после ретрая; исчерпание →
   FetchError).
4. **kcar введён в бой в стеке**: источник включён (enabled, cron */15,
   timeout 60s, page_size 100), образ worker_fetch пересобран (в старом
   был мёртвый эндпоинт `/dst/...`), circuit breaker сброшен.
   Live: `POST /bc/search/list` → **100 новых объявлений сохранено**
   (в БД kcar=102, encar=44).
   Нюансы эксплуатации: circuit breaker (5 ошибок подряд + cooldown 10м)
   сбрасывается `UPDATE source_health SET error_streak=0 ...`; воркер
   читает список источников на старте — после включения источника нужен
   `docker compose restart worker_fetch`.

**450 тестов зелёные, ruff clean.**



### 31. UX-ремонт бота: панель диалога, мини-апп, Китай, кнопочный фоллбэк
1. **Вал сообщений → «живая» панель**: каждый ход ассистента (и каждый шаг
   кнопочного фоллбэка) редактирует предыдущее сообщение на месте
   (`panel_msg_id` в FSM), новые сообщения — только при первом шаге.
2. **JSON-критерии → человекочитаемый список**: `format_criteria` с
   русскими лейблами, ценами с пробелами и странами-флагами
   (`{'country': 'CN'}` → «Страна: 🇨🇳 Китай»). Убран `json.dumps` из текста.
3. **Китай вернулся**: опции стран генерировал LLM без ограничений —
   в SYSTEM_PROMPT добавлен канонический список (Корея/Китай/Германия/
   США/Япония) и требование давать опции строго из него.
4. **Мультивыбор больше не ломает ход**: кнопки опций несут токен хода
   (`dlg:opt:{turn}:{idx}`), клик по устаревшей кнопке отвечается
   «устарела» и игнорируется.
5. **Кнопки всегда при активном ассистенте**: «🔎 Запустить поиск»
   появляется и по эвристике заполненности (make/model + цена + страна),
   а не только когда LLM решил; плюс «📱 Мини-приложение» и «🔄 Сбросить».
6. **Мини-апп доступен и на http**: `MINIAPP_URL` без https (локальная
   демка) раньше давала пустую клавиатуру — теперь url-кнопка
   (браузер); web_app-кнопка — только для https.
7. **Багфиксы**: (а) клик по опции слал в диалог `user_id` **бота**
   (id брали из `cb.message.from_user`) — ход диалога шёл в чужую
   сессию, «после выбора страны ничего не происходило»; теперь
   `user_id` передаётся явно из `cb.from_user`. (б) в кнопочном
   фоллбэке вообще не было обработчика `btn:`-колбэков — инлайн-кнопки
   не работали; добавлен `btn_click` с маршрутизацией по полю.

Тесты: `tests/test_bot_dialog_ui.py` — 7 шт (формат, эвристика,
токены хода, кнопка поиска по эвристике, http/https мини-апп).
**457 тестов зелёные, ruff clean.** Бот пересобран и задеплоен в стек.

### 32. Инцидент: AI-режим 502 + кнопка мини-аппа не появлялась
1. **502 на /api/dialog/message**: в mono-стеке одновременно с backend был
   поднят gateway (micro-профиль) с DNS-алиасом `backend` — имя `backend`
   резолвилось в два контейнера (round-robin), запросы бота случайным
   образом уходили в gateway и зацикливались → 502. Фикс: gateway
   остановлен (`docker compose rm -sf gateway`); в mono он не нужен.
   ПРАВИЛО: gateway и backend одновременно не поднимать — алиас конфликтует.
2. **Кнопка мини-аппа**: `_miniapp_url()` фильтровал не-https в пустоту,
   из-за чего fallback на url-кнопку в `_miniapp_button` был недостижим.
   Теперь `_miniapp_url` возвращает сырой URL, схема проверяется в
   `_miniapp_button` (https → web_app, http → url-кнопка).

### 33. Исследование CarAPIS (проверено вживую, не интегрировано)
Источник: https://carapis.com/ru/developers/ — каталог 2.8M объявлений
(~80 площадок, единая JSON-схема + оценка относительно рынка).
- **MCP-сервер (ключевая находка)**: `https://mcp.carapis.com/mcp/` —
  бесплатно, БЕЗ ключа и без дневного лимита. Инструменты: search
  (бренд/модель/годы/цена USD/пробег/площадка, limit≤25), get по UUID,
  facets, market_summary (min/медиана/max по срезу).
- **Покрытие (facets по source)**: encar 113 633, kcar 8 780.
  **che168 и китайских площадок НЕТ.**
- **Качество данных**: поля id/source/brand/model/trim/year/mileage/
  fuel_type/transmission/body_type/color/price_usd + valuation
  {estimated_usd, low/high, confidence_0_1, percentile_rank,
  comparable_count, is_undervalued}. Пример: Palisade 2022, 48.8k км,
  $18 394 при оценке рынка $24 842 (undervalued, 170 компарабелей).
- **Ограничения**: в бесплатном MCP нет URL и ID исходного объявления
  (только внутренний UUID) → для синхронизации непригоден; поиск
  медленный (~30 с на запрос) — не годится в реальном времени;
  REST-тариф: 100 запросов/день бесплатно (с URL объявлений),
  Production 5k/день — цена в кабинете.
- **Вывод**: парсеры encar/kcar не заменять. Ценность — фича «оценка
  рынка» (undervalued-лоты, медианные цены при финализации критериев)
  или инструмент AI-агента. Решение по интеграции отложено.

## 34. MVP-разворот: Корея-only, менеджер Артём, фото-карточки, калькулятор, nginx+SSL

Разворот прототипа под главную идею: бот → WebApp (пользователь выбирает
понравившийся UI/UX). Всё не-корейское выпилено.

### Backend
- **Калькулятор доставки Корея → РФ** (`app/services/delivery.py`,
  `app/api/calc.py`): POST /api/calc/delivery {price_usd, engine_cc, year} →
  смета «под ключ» (авто, море, брокер, СБКТС/ЭПТС, пошлина по возрастной
  группе и €/см³, сбор, утильсбор); POST /api/calc/compare — сравнение
  вариантов с сортировкой по итогу. Ставки — константы модуля (курс 92 ₽/$,
  1.08 $/€, море 1800 $, брокер 250 $, СБКТС 350 $; льготный утильсбор
  3400/5200 ₽, >3000 см³ — полная 136 200 ₽).
- **Лента только Корея**: GET /api/listings без `source` → encar+kcar.
- **AI-агент**: новый промпт — «Артём, менеджер по авто из Кореи»
  (проактивный, Корея-only, расчёты — только через инструмент); новый tool
  `calc_delivery` (schema в tools.py, исполнение в agent.py).

### Бот
- **Фото-карточки**: после «Поиск запущен» (dlg:finalize) — топ-5 объявлений
  фото-хотлинками (sendPhoto по URL источника, ничего не храним), caption +
  кнопки «🔗 Открыть объявление» / «🚚 Доставка и растаможка» / «📱 Приложение».
  Фолбэк на текстовую карточку, если фото источника недоступно.
- **Калькулятор в боте**: /calc [цена $ объём год], кнопка «🧮 Калькулятор» в
  меню, callback calc:{id} прямо с фото-карточки (KRW/EUR → USD по фикс-курсу).
- **Кнопочный fallback**: шаг «страна» удалён (4 шага, всегда KR).
- /start: приветствие менеджера, Корея-only.

### Mini App (frontend)
- Новый экран **/calc** (screens/Calc.tsx): форма (цена/объём/год) → градиентная
  карточка «Итого под ключ» + разбивка + дисклеймер; преилл из query
  (?price=&cc=&year=). Кнопка «Доставка и растаможка» в карточке объявления.

### nginx + самоподписанный SSL
- frontend/nginx.conf: 80 + 443 ssl (прокси /api → backend:8000);
  frontend/entrypoint-ssl.sh — генерация self-signed при старте
  (SAN = DNS:localhost + IP:$NG_HOST_IP); compose: 8443:443,
  NG_HOST_IP=${LAN_IP}. .env: MINIAPP_URL=https://10.132.137.38:8443,
  LAN_IP=10.132.137.38.
- ⚠️ **Ограничение Telegram**: web_app-кнопка требует ДОВЕРЕННЫЙ сертификат —
  с self-signed нативный WebApp в клиентах Telegram может не открыться
  (url-кнопки работают всегда). Для полноценного web_app нужен домен +
  Let's Encrypt (следующий шаг).

### Проверки
- 463 теста зелёные (включая новые tests/test_delivery.py), ruff чист.
- Живьём: https://localhost:8443 → 200; /api/listings → только encar/kcar;
  /api/calc/delivery и /api/calc/compare считают; бот с MINIAPP_URL=https.
- Грабли: CRLF в entrypoint-ssl.sh (Windows) → «no such file or directory»;
  лечится `sed -i 's/\r$//'` перед сборкой.

## 35. Полное тест-покрытие MVP-калькулятора (+26 тестов → 489)

- **tests/test_calc_api.py** (9): /api/calc/delivery (200 + числа: пошлина
  5832 $, 7 компонентов, тотал > цены, ₽/$ согласованы; default engine_cc;
  4×валидация 422), /api/calc/compare (сортировка по итогу, пропуск битых
  items, автотайтл, пустой список → 422).
- **tests/test_bot_calc.py** (14): _price_usd (KRW/EUR/USD/мусор),
  _format_calc_out (7 строк, группировка «18 000 $»), _miniapp_button с path
  (https → web_app с query, http → url-фолбэк), _miniapp_kb,
  _car_card_kb (🔗/🚚/calc:{id}, без url/miniapp), build_listing_caption
  (KRW-цена, отсутствие цены, лимит 1024).
- **tests/test_agent.py** (+3): tool calc_delivery — полная разбивка 7 строк
  (агент не выдумывает цифры), битые аргументы → просьба уточнить,
  схема tool для LLM (required price_usd+year).
- **Найденные тестами баги (исправлены)**: «18,000 $» вместо «18 000 $»
  в сметах бота (_format_calc_out) и format_breakdown — запятые десятичного
  разделителя локали f-string не заменялись на пробелы.

## 36. Сравнение «под ключ» end-to-end + тестовая база фронтенда (+15 тестов → 504 py + 9 vitest)

**Фича сравнения ( ранее только API):**
- бот: `/calc A vs B` (разделители `vs`/`;`/`|`/перенос) → `POST /api/calc/compare`, медали + дельта; `client.compare_delivery`
- бот: меню «⚖️ Сравнить» → хинт + миниапп `/compare`; команда `/calc` добавлена в set_my_commands
- вебапп: экран `/compare` (2–4 варианта, бары, «лучший итог»), кнопка из калькулятора с сид-параметрами, deep-link `?v=18000x2000x2022,...`

**Тесты:**
- `tests/test_bot_calc.py` +9: интеграция `cmd_calc` (один вариант, 2 числа, «18 000», vs-сравнение, фоллбэк при недоступном бэкенде, мусор→help), `menu:compare`/`menu:ai`; фейки `FakeMsg`/`FakeCb`
- фронтенд: vitest (`npm test`), `src/lib/fx.ts` — общие чистые хелперы (`toUsd`, `digits`, `parseQueryVariants`); Listing/Compare переведены на него; `fx.test.ts` 9 тестов
- пойманные тестами баги: regex `\d[\d ]*` склеивал числа в одно (парсер вариантов переписан + склейка тысяч «18 000»); `parseQueryVariants` терял год в коротком формате `18000x2022`

Итог: `pytest` → **504 passed**, `vitest` → **9 passed**, ruff/mypy clean, bot+miniapp пересобраны.

## 37. Бот «идеал»: ссылка → карточка, антифлуд, error-handler (+16 тестов → 520)

- **Ссылка на объяву → карточка**: `GET /api/listings/by-url` (нормализация: схема/www/m./слеш), `client.get_listing_by_url`; `fallback_text`: URL → фото-карточка с кнопками (расчёт/миниапп), не найдено → AI. `/help` с примерами `/calc` и «скиньте ссылку»
- **Антифлуд**: `app/bot/throttle.py` — `FloodGate` (скользящее окно 15 событий/10 с, чистая функция времени) + `FloodMiddleware` на message/callback_query (дроп, варн раз в окно)
- **`@dp.errors`**: лог + «😔 Что-то пошло не так…» вместо тишины; polling не падает
- Тесты +16: throttle (8: лимит, окно, независимость юзеров, миддлварь), by-url (4), fallback-ссылки (4); test-найденный баг: первый `should_warn` блокировался дефолтом 0.0
- Живая проверка: `by-url?url=https://encar.com/e2e-1` → 200 BMW 520i; 404 для отсутствующих

## 38. AI-анализ стоимости: LLM-разбор + график, монетизация 1/нед (+33 теста → 553)

- **Сервис** `app/services/price_analysis.py`: comps (та же make+model, год ±2, до 30 шт, медиана/min/max), история цен, промпт на строгий JSON; `parse_llm_json` терпит ```-заборы; LLM упал/мусор → статфолбэк (вердикт по медиане ±7%, диапазон 0.93–1.07 median). Кэш разбора общий на листинг (LLM дорогой)
- **Модель + миграция 0015**: `price_analyses` (listing_id, user_id, verdict, fair_min/max, summary, payload JSONB, created_at; 2 индекса). Монетизация: free = 1 разбор (user, listing) в 7 дней, pro/business = безлимит (`quota_left`, `QuotaExceeded` → 403 `quota_exceeded`)
- **API** `app/api/analysis.py`: `POST /api/listings/{id}/analysis` (JWT, LLM+сохранение), `GET` (кэш, без LLM), `POST /api/bot/listings/{id}/analysis` (X-Internal-Token, body user_id) — оба роутера в main.py
- **Бот**: кнопка «🤖 Анализ цены» на фото-карточке (`analyze:{id}`, рядом с 🚚), хендлер: «Анализирую…» → вердикт (🟢/🟡/🔴), диапазон, отклонение %, факторы, медиана рынка + кнопка миниапп `/listing/{id}` (график). 403 → апселл Pro + миниапп; backend down → вежливое. `client.analyze_price` — своя реализация (120 с, 403 ≠ ошибка). `/help` дополнен
- **WebApp/сайт**: секция «🤖 AI-анализ цены» в Listing.tsx: GET кэша при открытии → сразу график; иначе кнопка «Анализировать (1 бесплатно в неделю)» → POST; 403 → замок + «⚡ Безлимит в Pro» → /profile. `lib/analysis.ts` (типы, `deltaPct`, `buildChartData` — чистые), `ui/PriceChart.tsx` — SVG-график: линия истории, точка текущей цены, зелёная зона рыночного диапазона (без chart-библиотек). CSS-блок `.analysis*`, `.price-chart*`
- **Тесты**: `tests/test_price_analysis.py` (10: парсинг JSON, пороги вердикта, квоты, LLM/статфолбэк), `tests/test_analysis_api.py` (6: POST 200/403/404, pro безлимит, GET кэш/None; FakeSession.get + `.first()` в conftest), `tests/test_bot_analysis.py` (7: формат, кнопка карточки, хендлер ок/квота/down/мусор), `frontend/src/lib/analysis.test.ts` (9: deltaPct, buildChartData, VERDICT_LABEL). **Итог: 543 pytest, 18 vitest; ruff чисто; mypy не хуже базы; фронт собирается**

## 39. Шлифовка UI/UX анализа (график, состояния, формат в боте) (+1 тест → 544)

- **График** (`PriceChart.tsx`): градиентная заливка под линией, подпись текущей цены у точки, даты первой/последней точки (дд.мм), компактные подписи шкалы (`formatCompact`: 18,4 млн / 950 тыс), пульс точки; иначе — подсказка «истории цены пока мало» (не прятать блок молча)
- **Секция анализа** (Listing.tsx): состояние загрузки — спиннер + скелетоны («Сравниваю с рынком…»), pill-бейдж отклонения (▼/▲ с цветом), шкала справедливого диапазона с анимированным маркером цены (`fairPosition`), строка меты «разбор от {дата} · нейросеть/статистика» (+`created_at` в payload/API) и «🔄 Пересчитать»; при квоте — «доступен снова через несколько дней»; fade-in секции
- **Бот**: `_format_analysis` реструктурирован — цена (💵), рынок (📊 count+медиана), год в заголовке, стрелка 📉/📈 у отклонения, футер с квотой и Pro
- **Тесты**: +formatCompact/fairPosition (analysis.test.ts 22 vitest), +test_format_analysis_title_with_year_and_money_lines; пойман баг formatCompact (18,4 млн → «18 млн»). **544 pytest / 22 vitest, ruff+mypy чисто, бандл на 8443 обновлён**

## 40. Боевое развёртывание MVP: секреты, fail-fast, deploy-кит (+4 теста → 548)

Цель — полный prod-контур: VPS + docker compose (mono) + Caddy (авто-Let's Encrypt).

- **Ротация секретов (INC-001, INC-002 ЗАКРЫТЫ)**: `APP_SECRET_KEY` был длиной 9 символов
  (= подпись JWT + fallback internal-токена). Сгенерированы: 64-hex `APP_SECRET_KEY`,
  отдельный 64-hex `INTERNAL_TOKEN`, сильный `DB_PASSWORD` (+`ALTER USER` в живой БД —
  volume уже инициализирован). backend/bot пересобраны и перезапущены; внутренний API:
  неверный токен → 401, верный → 200. Старые JWT инвалидированы (перелогин через initData).
- **Fail-fast (INC-004 ЗАКРЫТ)**: `Settings._prod_secrets_required` — при `APP_ENV=prod`
  падаем на старте, если ключ = дев-дефолт или короче 32 символов (`tests/test_prod_secrets.py`).
- **INC-005 (прод)**: в боевом оверрае TLS терминирует Caddy (авто-LE по `$DOMAIN`),
  self-signed 8443 остаётся только в dev.
- **Deploy-кит `deploy/`**:
  - `docker-compose.prod.yml` — оверрайд: Caddy на 80/443 → miniapp → /api; порты инфры
    (postgres/redis/rabbitmq) и backend сняты с публикации (`!reset []`); `restart: always`;
    ротация логов json-file 10 MB × 3; `APP_ENV=prod`, публичный `MINIAPP_URL=https://$DOMAIN`.
  - `Caddyfile` — авто-сертификат, gzip, `/health`.
  - `bootstrap.sh` — VPS одной командой: Docker, генерация `.env` с боевыми секретами,
    UFW (наружу только 22/80/443), подъём, миграции, cron бэкапа.
  - `backup.sh` / `restore.sh` — pg_dump daily (14 копий) + восстановление.
  - `DEPLOY.md` — ранбук: DNS → bootstrap → проверка LE → обновления → бэкапы → мониторинг.
- **Тесты**: тесты internal-API переведены с `get_settings().app_secret_key` на
  `deps_internal.internal_token()` (единый источник истины — теперь ротация INTERNAL_TOKEN
  не ломает их). **548 pytest (+4), ruff чисто, mypy = база**
- **Live**: стек на новой машине поднят с нуля после рестарта Docker — backend healthy,
  бот поллит, miniapp 200. Для выхода в прод: VPS + домен + `bootstrap.sh` (DEPLOY.md).

## 41. Скрипты запуска + гостевой режим сайта (+2 pytest → 550, +18 vitest → 40)

- **Скрипты запуска**: `start.bat` (Windows) / `start.sh` (Linux) — инфраструктура →
  `--profile mono up -d --build` → миграции → ожидание /health → подсказка URL.
  Для VPS остаётся `deploy/bootstrap.sh`.
- **Гостевой режим (незалогиненный посетитель может всё потрогать)**:
  - Бэк: `POST /api/auth/guest` — временный юзер («Гость», синтетический id < 0,
    без email/пароля, явно role=buyer/tier=free/is_active) → обычная JWT-пара.
    Гость = free-юзер: лента, фильтры, карточка, **AI-разбор цены 1/нед**,
    калькулятор, избранное. Нет только Telegram-уведомлений и Pro.
  - Фронт: вне Telegram `authorize()` → null → тихий `guestLogin()` → полный UI
    без формы входа; скип онбординга; дисмиссабельный баннер «Демо без регистрации»
    со ссылкой /login; роут `/login` (только для гостя) — LoginScreen, после входа
    гостевые токены заменяются (`clearGuest`); в Профиле — гостевой блок
    «Войти / Регистрация».
  - Публичные GET и так открыты: лента/карточка/кэш разбора/калькулятор.
- **Тесты сайта (vitest)**: `vitest.config.ts` (jsdom + react-плагин);
  dev-deps jsdom + @testing-library/react. Новое: `api/client.test.ts` (токены,
  Bearer, 204, ApiError/detail/statusText), `api/auth.test.ts` (Telegram-контекст,
  гостевой вход/выход, webLogin/webRegister, 409), `App.guest.test.tsx`
  (компонентные: гость→лента+баннер, крестик баннера, гостевой /login,
  у TG-юзера гостя нет). Стабы matchMedia/IntersectionObserver. **40 vitest / 550 pytest**
