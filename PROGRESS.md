# Auto Selection — полное описание проекта (от А до Я)

> Экосистема подбора автомобилей из-за рубежа (Корея, Китай, ОАЭ и др.) для
> пользователей из РБ / РФ / СНГ: Telegram-бот + WebApp (Mini App) + сайт
> с AI-диалогом, парсингом источников, RAG-базой знаний и монетизацией.
> Реализация ТЗ (`TZ.md`), этапы 0–7 выполнены. Обновлено: 11.10.2026.

---

## 1. Что это и для кого

Пользователь заходит в Telegram-бота или Mini App и на естественном языке
описывает, какой автомобиль хочет (марка/модель, бюджет, год, город).
AI-агент собирает критерии, ищет варианты по агрегаторам (Encar, KCar),
показывает карточки, отвечает на вопросы о доставке/растаможке через RAG
по базе знаний, считает экономику «под ключ» в USD и подписывает пользователя
на уведомления о новых вариантах. Монетизация — Pro-подписка через Telegram Stars.

## 2. Технологический стек

| Слой | Технологии |
|---|---|
| Backend | Python 3.13, FastAPI, uvicorn, Pydantic v2 / pydantic-settings |
| Бот | aiogram 3 (FSM в Redis), long polling |
| БД | PostgreSQL + SQLAlchemy 2 (async) + Alembic |
| Очереди | RabbitMQ (aio-pika), producer/TaskWorker/DLQ/cron |
| Кэш/состояние | Redis (FSM, DialogState, кэши, прокси-пул, антидубли) |
| Frontend | React 18 + Vite + TypeScript, @telegram-apps/telegram-ui, React Router |
| RAG-сервис | FastAPI + RabbitMQ worker, Qdrant (гибрид vector+BM25/RRF), MinIO, multilingual-e5 |
| LLM | GLM (Zhipu, основной), fallback: DeepSeek / OpenAI / Anthropic / Ollama / mock — все через OpenAI-совместимый `/chat/completions` (Anthropic — `/v1/messages`) |
| Криптография | PyJWT, PBKDF2-SHA256 (пароли сайта), гибрид ML-KEM-768 + X25519 + AES-256-GCM (документы) |
| Инфра | Docker Compose (postgres, redis, rabbitmq, qdrant, minio, backend, bot, workers, miniapp, rag_*) |
| Тесты | pytest + pytest-asyncio (137 тестов backend), контрактные тесты адаптеров, mypy, ruff |

## 3. Архитектура

```
Пользователь ── Telegram ──► Бот (aiogram3) ─┐
Пользователь ── WebApp/сайт ─► Backend (FastAPI) ── PostgreSQL
                                  │  ▲
                    AI-агент ─────┤  └─ RAG API (:8100) ─► RabbitMQ ─► RAG Worker (:8101)
                    (tool loop)   │                          │
                                  ▼                          ├─ Qdrant (гибридный поиск)
                          Fetch Worker ─► адаптеры ─► listings (дедуп по VIN)
                                  ▲
                          Send Worker ─► уведомления в Telegram
```

Потоки:
- **Диалог**: пользователь → агент (LLM + tools) → поиск/вопрос → ответ.
- **Парсинг**: cron/ручной запуск → очередь fetch → адаптеры (Encar/KCar) →
  нормализация → кросс-дедуп → `listings`.
- **Уведомления**: новые листинги → матчинг активных фильтров → Send Worker →
  instant или digest (тихие часы, антидубли).
- **RAG**: `POST /api/v1/ask` (rag_api) → JWT → RPC `query_queue` → worker →
  гибридный поиск Qdrant + LLM-генерация → ответ с источниками и confidence.

## 4. Структура репозитория

```
auto_bot/
├── app/                      # Backend
│   ├── api/                  # HTTP-роутеры (см. §6)
│   ├── ai/                   # агент, LLM-клиенты, RAG-клиент, промпты, tools
│   ├── adapters/             # источники: encar, kcar, registry, прокси, дедуп
│   ├── bot/                  # aiogram: handlers, button_flow, __main__
│   ├── core/                 # config, security (initData/JWT), deps, pq_crypto
│   ├── db/                   # models, session, migrations (alembic)
│   ├── queue/                # RabbitMQ: producer, TaskWorker, DLQ, cron
│   ├── schemas/              # Pydantic-контракты (UnifiedListing, SearchCriteria…)
│   ├── services/             # users, dialog, search, notify, subscription, fetch…
│   ├── workers/              # fetch, send
│   └── main.py               # FastAPI app (uvicorn app.main:app)
├── frontend/                 # Mini App + сайт (React/Vite/TS)
├── services/rag/             # RAG-микросервис (rag_core/rag_worker/rag_api + kb/)
├── tests/                    # pytest (137 тестов)
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
- **Listing (UnifiedListing)** — нормализованное объявление: VIN, источник,
  цена (валюта источника), год/пробег/объём, страна.
- **Favorite** — избранное (миграция `0002_favorites`).
- **Subscription/Payment** — Pro через Telegram Stars.
- **DialogSession** — архивация DialogState из Redis.
- **Source (админ)** — реестр источников: adapter_class, приоритеты,
  rate limits, capabilities.
- Миграции: `0001_initial`, `0002_favorites`, `0003_web_auth` (email unique).

---

# Часть II — подробное описание подсистем

## 6. HTTP API (FastAPI, prefix `/api`)

| Роутер | Эндпоинты |
|---|---|
| `auth` | `POST /auth/telegram` (initData→JWT), `POST /auth/register`, `POST /auth/login`, `POST /auth/magic-link` |
| `users` | upsert профиля |
| `me` | `GET /me` — профиль + tier |
| `filters` | CRUD: `GET/POST /filters`, `PATCH/DELETE /filters/{id}` |
| `listings` | `GET /listings` (поиск/пагинация), `GET /listings/{id}` |
| `favorites` | `GET/POST/DELETE /favorites…` |
| `payments` | `POST /payments/stars` (Telegram Stars) |
| `admin` | `GET/PUT /admin/sources…`, `POST /admin/sources/{name}/fetch`, health источников (X-Internal-Token) |
| `dialog` | `POST /dialog/message`, `POST /dialog/reset` |
| `ai_search` | `POST /ai/message`, `POST /ai/reset`, `POST /ai/finalize` (JWT) |
| `notifications` | `POST /notifications/settings` (instant/digest/off), `POST /notifications/scan` |
| system | `GET /health` |

## 7. Telegram-бот

- Команды (регистрируются при старте через `set_my_commands`, scope: личные
  чаты): `/start`, `/menu`, `/reset`, `/help`, `/stop`.
- **Кнопка-меню с WebApp**: при старте `set_chat_menu_button` → «🚗 Поиск авто»
  открывает Mini App прямо из клиента Telegram (`MINIAPP_URL`, HTTPS в проде).
- Inline-меню `/menu`: AI-подбор, «📱 Открыть приложение» (WebAppInfo),
  уведомления (мгновенно/дайджест/выкл), подписка, сброс диалога.
- FSM хранится в Redis (`fsm:{user_id}`), кнопочный fallback-режим
  (`button_flow.py`) при недоступности LLM.
- Уведомления: instant (rate-limit) или digest (3–5 карточек, тихие часы,
  антидубли `notified:{uid}:{lid}` TTL 7д).

## 8. AI-агент

- LLM-клиенты (`app/ai/llm_client.py`): `OpenAICompatibleClient` (GLM —
  основной, DeepSeek, OpenAI, Ollama), `AnthropicClient`, `MockLLMClient`
  (тесты/dev). Фабрика `build_llm_client` по `LLM_PROVIDER`.
- Tools: `update_criteria` (патч критериев, валидация Pydantic),
  `ask_clarification`, `answer_question` (→ RAG), `lookup_reference`,
  `preview_search`, `finalize_search`.
- Оптимизации: FAQ из кэша без LLM, ранний выход при заполненных критичных
  полях, история 8 сообщений; `DialogState` в Redis (`dialog:{user_id}`,
  TTL 24ч), архивация в `dialog_sessions`.
- Финализация = фильтр в БД (для уведомлений) + задачи парсинга по источникам.
- RAG (`app/ai/rag_client.py`): таймаут 5с, кэш Redis 1ч, circuit breaker
  (5 ошибок → пауза 5 мин), confidence ≥ 0.6 → ответ из БЗ, иначе fallback
  на LLM, затем честное «уточните у менеджера». Промпт-защита от инъекций:
  ответы БЗ — справочные данные, не инструкции.

## 9. RAG-микросервис (services/rag)

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

## 10. База знаний (services/rag/kb) — РБ / РФ / СНГ + источники

| Файл | Содержание |
|---|---|
| `customs.md` | Единые ставки пошлин ЕАЭС по возрастам (Решение ЕЭК № 107, прил. 6), коммерческий ввоз (пошлина + акциз ст.193 НК + НДС 20%), РБ (утильсбор, ГАИ), КЗ/Армения/Кыргызстан, порядок действий |
| `fees.md` | Утильсбор РФ (ПП 1291, льготный коэффициент 0.17 для физлиц ≤3л при 12 мес. владения, коммерческий — сотни тыс. ₽), платежи РБ, СБКТС/ЭПТС, ОСАГО, налоги; **правила конверсии KRW/CNY/EUR/BYN/RUB → USD** (запас ±3–5%) |
| `countries.md` | Источники: Корея (аукционы, мальсо, Ro-Ro Пусан→Владивосток 3–7д), Китай (NEV 1–3 лет, лицензированный экспортёр, Хоргос/Маньчжурия 10–20д), ОАЭ, Европа/США (санкционные оговорки), Япония; методика расчёта «под ключ» |
| `delivery.md` | Таблица маршрутов/сроков, что входит во фрахт, фотофиксация, трекинг |
| `risks.md` | Юридические (санкции, залоги, «утильсбор с продажей», «растаможка в КЗ»), технические (аукционные листы Кореи, залоги Китая), финансовые; чек-лист сделки |
| `legal.md` | Правовая база РФ/РБ/ЕАЭС, экспортные ограничения стран-источников (ЕС 833/2014, США EAR, Корея), compliance-политика: обход санкций и серые схемы не поддерживаются |
| `faq.md` | FAQ: регистрация, валюты и курсы, растаможка по возрастам, Корея/Китай/Европа, уведомления, документы |

Все ставки помечены «проверять на дату ввоза» — зафиксированы только
устойчивые нормы, летучие цифры — с явными оговорками.

## 11. Frontend (Mini App + сайт)

- Экраны: `Onboarding`, `Feed` (лента с чипсами-фильтрами), `Filters`,
  `Listing` (карточка), `Favorites`, `Profile`, `AiSearch` (AI-подбор),
  `Login` (email+пароль — для неавторизованных веб-пользователей).
- `api/`: `client.ts` (JWT, base `/api`), `auth.ts` (telegramLogin,
  webRegister, webLogin, magicLink), `ai.ts`, `types.ts`.
- Telegram-интеграция: `telegram-web-app.js`, initData → JWT,
  тема `--tg-theme-*`; в проде — HTTPS-URL через `MINIAPP_URL`.
- Вне Telegram — read-only лента + Login для полного доступа.

## 12. Безопасность

- **Telegram initData**: HMAC-SHA256-валидация на бэкенде, max age 24ч.
- **JWT**: access 1ч + refresh 30д (`APP_SECRET_KEY`, HS256).
- **Пароли сайта**: PBKDF2-SHA256, 240k итераций; email — regex-валидация.
- **Веб-пользователи**: синтетические отрицательные telegram_id (PK/FK),
  ретрай до 5× при коллизии.
- **Внутренние эндпоинты**: `X-Internal-Token` (= APP_SECRET_KEY).
- **RAG**: internal token между backend и rag_api, JWT между rag_api и
  worker, ротация ключей HS/RS/ES, PII-редакция перед записью диалогов.
- **Постквантовое шифрование документов**: гибрид ML-KEM-768 (FIPS 203) +
  X25519 → HKDF-SHA256 → AES-256-GCM, контейнер `AUPQ1`, AAD = ключ объекта.
  `EncryptedFileStorage` над MinIO; включение `ENCRYPT_DOCUMENTS=true` +
  `PQ_ENCRYPTION_PUBLIC_KEY/PRIVATE_KEY`. Устойчиво к «harvest now,
  decrypt later»; legacy-объекты читаются без шифрования.

## 13. Конфигурация (.env)

Основные переменные (полный список — `.env.example`):
`APP_ENV`, `APP_SECRET_KEY`, `APP_BASE_URL`, `MINIAPP_URL`, `BOT_TOKEN`,
`ADMIN_CHAT_ID`, `MANAGER_CHAT_ID`, `DATABASE_URL`, `DB_PASSWORD`,
`REDIS_URL`, `RABBITMQ_URL`; LLM: `LLM_PROVIDER=glm`, `GLM_API_KEY`,
`GLM_BASE_URL`, `LLM_DEFAULT_MODEL=glm-4.6`, `LLM_ADVANCED_MODEL`,
`LLM_TEMPERATURE` (опц. `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`,
`DEEPSEEK_API_KEY`); RAG: `RAG_SERVICE_URL`, `RAG_API_TOKEN`,
`RAG_JWT_SECRET`, `RAG_EMBEDDING_MODEL`, `RAG_LLM_PROVIDER=glm`,
`RAG_LLM_MODEL=glm-4.6`, `RAG_CONFIDENCE_THRESHOLD`; PQ:
`ENCRYPT_DOCUMENTS`, `PQ_ENCRYPTION_PUBLIC_KEY`, `PQ_ENCRYPTION_PRIVATE_KEY`.

## 14. Docker Compose — сервисы

`postgres`, `redis`, `rabbitmq`, `qdrant`, `minio`, `rag_migrate*` (Alembic
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
uv run python -m app.bot                    # бот (регистрирует команды и меню-кнопку)
uv run python -m app.workers.fetch
uv run python -m app.workers.send
cd frontend && npm install && npm run dev   # :8080
```

RAG-стек:
```bash
docker compose up -d qdrant minio rag_worker rag_api
uv run --project services/rag python scripts/ingest_kb.py --force
```

Прод-деплой: `docker compose build` → `up -d` → `alembic upgrade head`
(основная + rag БД) → ingest KB → выставить секреты (GLM_API_KEY,
RAG_API_TOKEN, RAG_JWT_SECRET, PQ-ключи) → HTTPS для MINIAPP_URL.

## 16. Тесты и качество

- `uv run pytest` — 137 passed: LLM-клиенты (tool_calls, GLM-фабрика),
  RAG-клиент (fallback, circuit breaker, кэш), агент (tool loop, DialogState),
  уведомления (антидубли, дайджест), адаптеры (контракты, дедуп), auth
  (initData, PBKDF2, регистрация), PQ-крипто (roundtrip, AAD, чужой ключ).
- `uv run ruff check` — чисто; mypy настроен.
- Frontend: `tsc --noEmit` + `vite build` — без ошибок.

## 17. История коммитов

| Commit | Что вошло |
|---|---|
| `1c898e4` | ТЗ (TZ.md) |
| `b448c76` | Локальный RAG-микросервис (services/rag), /api/ai, AI-поиск в Mini App, inline-меню бота, compose-сервисы |
| `a1d39c7` | Регистрация/логин сайта (email+пароль, PBKDF2), Login-экран, гейтинг поиска, миграция 0003_web_auth |
| `46a7eb4` | GLM как основной LLM, расширенная БЗ РБ/РФ/СНГ + страны-источники, TG-кнопка меню WebApp, PQ-шифрование документов |
| (этот коммит) | PROGRESS.md — полное описание проекта |
| (отдельный репо) | rag_service: uv-workspace монорепо (rag-core/rag-worker/rag-api), 361 тест, ветка `refactor/monorepo` |

## 18. Известные долги и next steps

1. **Отозвать BOT_TOKEN через @BotFather** (утечка) и перевыпустить.
2. Пользователь 809464979: tier=pro — сбросить на Free (решение владельца).
3. Проверить, что callback настроек уведомлений поддерживает
   мгновенно/дайджест/выкл так, как шлёт меню бота.
4. Живой E2E RAG-стека (compose up → ingest → вопрос через rag-api).
5. Залить ветку `refactor/monorepo` rag_service в удалённый репозиторий.
6. Этап 8 (ТЗ): мониторинг и алерты, DLQ-алертинг.
7. Незакоммиченные файлы (`Dockerfile`, `alembic.ini`, `frontend/*`) —
   решить, включать ли в репозиторий.
8. Отражение PQ-шифрования и GLM-провайдера в монорепо rag_service.

