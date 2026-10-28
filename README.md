# Auto Selection — подбор авто из Кореи: Telegram Mini App + партнёрский кабинет + бот

Продукт для тех, кто зарабатывает на автомобилях из Кореи: перекупов,
маклеров/брокеров и частных продавцов. Агрегирует объявления (encar.com;
конвейер парсинга рассчитан на несколько источников), тянет «как в Корее»
чеки осмотра и историю машины, добавляет AI-анализ цены, чаты с продавцами
и партнёрами, внутренний рынок с размещениями и монетизацию (Stars, CPL).
Внутри — Telegram-бот, Mini App, партнёрский кабинет и админка на одном
бэкенде.

## Три сценария (зачем это каждому)

1. **Пользователь (перекуп/частник)** — открывает Mini App из бота: лента
   объявлений Кореи с фильтрами и бейджами чеков, AI-поиск диалогом,
   карточка с двумя ценами ($ и ₽), картой повреждений и AI-разбором
   цены («выгода / справедливо / переплата» + факторы), «Отчёт истории»
   (ДТП/владельцы/пробег), избранное, история, калькулятор «под ключ»
   (доставка/растаможка РФ/РБ), сравнение; подписывается на «сигналы» —
   бот присылает новые лоты и смены цен.
2. **Партнёр (салон/маклер)** — кабинет `/partner` по API-ключу: пул
   лидов с batch-claim и фильтрами, мои лиды со статусами, чаты с
   клиентами, рынок с медианами и «выгодными» лотами, витрина,
   объявления (пакеты размещений со счёта), конструктор чеков осмотра
   «как в Корее» (диаграммы с пинами, чек-лист 12 агрегатов), биллинг.
3. **Владелец объявления (частник)** — «+ Продать» в Mini App: визард
   Авто → Фото → Цена с загрузкой фото в S3, «Сгенерировать описание» и
   live «Качество объявления»; жизненный цикл (черновик → модерация →
   публикация → архив/истечение), бесплатное размещление 7 дней, дальше
   20★/нед или пакет на платном тарифе; чаты с покупателями и
   переговорные до 5 участников по инвайт-кодам.

## Карта репозитория

| Каталог | Что внутри (1 строка) |
|---|---|
| `app/` | весь Python-бэкенд по слоям Clean Architecture: `domain/` (правила), `application/` (сценарии, 6 доменных групп), `infrastructure/` (db/sources/workers/storage/messaging), `transport/` (HTTP-роутеры, бот), `di/` (Dishka) |
| `services/` | тот же код в профиле micro: gateway + доменные сервисы (identity/catalog/billing/partners/dialog/admin) + `translate/`, `rag/` |
| `frontend/` | Mini App + партнёрка + админка: React 18 + telegram-ui (`src/screens`, `src/components`, `src/partner`, `src/admin`) |
| `docs/` | документация проекта (карта ниже) |
| `deploy/` | боевой рантайм: Caddyfile, prod/test compose, bootstrap/backup-скрипты |
| `configs/` | конфиг Centrifugo (шаблон с env-подстановкой) |
| `scripts/` | утилиты: туннель миниаппа (miniapp_tunnel.ps1), backfill-скрипты инспекций, разовые фиксы данных |
| `tests/` | pytest-сьюты, зеркально архитектуре (identity/catalog/billing/partners/dialog/admin/infrastructure/…) |
| `app/infrastructure/db/migrations/` | 30 миграций PostgreSQL (Alembic) |

## Стек

| Слой | Технологии |
|---|---|
| Backend | Python 3.13, asyncio, FastAPI, SQLAlchemy 2 (async), Alembic, Dishka (DI), aio-pika + FastStream (очереди) |
| Бот | aiogram 3 (long polling), inline-режим для шаринга карточек |
| Frontend | TypeScript, React 18, @telegram-apps/telegram-ui, react-router, Vite, vitest + testing-library |
| Данные | PostgreSQL 16, Redis 7 (кэш/лимиты/дедуп), RabbitMQ 3 (fetch/send), MinIO/S3 (фото + raw-payload), Qdrant (RAG) |
| Realtime | Centrifugo v6: один личный канал `user:{id}` / `partner:{id}`, поллинг-фоллбэк |
| AI | LLM GLM/Anthropic/Ollama (анализ цены, отчёты, описания, выжимки), RAG-бот поддержки, NLLB-200 (перевод ko→ru) |
| Инфра | Docker Compose (профили mono/micro + ступени), uv (Python), npm (frontend), GitHub Actions (pytest/ruff/pip-audit/bandit) |

## Быстрый старт (Docker)

```bash
cp .env.example .env                  # BOT_TOKEN, APP_SECRET_KEY, DSN, MinIO-креды
docker compose up -d --profile core   # postgres+redis+backend(монолит)+bot+miniapp
cd frontend && npm ci && npm run dev  # Mini App для разработки: :8080
```

Минимум для живого продукта — профиль `core`; дальше по потребности
(ступени и деградация — [docs/OPERATIONS.md](docs/OPERATIONS.md)):

```bash
docker compose --profile storage up -d   # + MinIO: фото и S3-зеркало
docker compose --profile workers up -d   # + RabbitMQ, парсинг/уведомления/перевод
docker compose --profile realtime up -d  # + Centrifugo: мгновенные чаты
docker compose --profile ai up -d        # + RAG/Qdrant
docker compose --profile micro up -d     # декомпозиция сервисами вместо монолита
```

Миграции применяются образом (`alembic upgrade head` в контейнере
backend/gateway) или локально: `uv run alembic upgrade head`.

Локально без Docker (полный список команд — OPERATIONS.md):

```bash
uv run uvicorn app.main_api:app --port 8000   # backend (mono)
uv run python -m app.transport.bot            # бот
uv run python -m app.infrastructure.workers.fetch && uv run python -m app.infrastructure.workers.send
```

Миниапп в Telegram требует публичный HTTPS — туннель одной командой
`miniapp-url.bat` (localhost.run; подробности и нюансы — OPERATIONS.md).

## Документация (docs/)

| Файл | О чём |
|---|---|
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | Clean-слои, 6 доменных сервисов, DI, личный канал Centrifugo, данные/деградация |
| [API.md](docs/API.md) | HTTP API: аутентификация (JWT/гость/API-ключ), эндпоинты пользователя/партнёра/админа |
| [SOURCES.md](docs/SOURCES.md) | парсинг: контракт адаптера, encar детально, registry, fetch-воркер, S3-зеркало фото, stale-чистка, перевод, как добавить источник |
| [NAV-V2.md](docs/NAV-V2.md) | навигация v4: лендинг всегда на «/», 4 таба мини-аппа, 5+«Ещё» партнёрки; как реализовано |
| [AI.md](docs/AI.md) | AI-контур: анализ цены, отчёт истории, генерация описаний, качество объявления, выжимки, RAG-бот |
| [MONETIZATION.md](docs/MONETIZATION.md) | тарифы Free/Pro/Business, размещения и пакеты, CPL-лиды, идемпотентность Stars |
| [OPERATIONS.md](docs/OPERATIONS.md) | запуск и деплой: ступени compose, .env, туннель миниаппа, мониторинг, прод-чеклист |
| [TESTING.md](docs/TESTING.md) | структура тестов, фикстуры, как запускать, известный флэйк |
| [SECURITY.md](docs/SECURITY.md) | модель безопасности; журнал аудита и инцидентов — [INCIDENTS.md](INCIDENTS.md) |
| [RUNBOOK.md](docs/RUNBOOK.md) | эксплуатационные сценарии: DLQ, circuit breaker, флейки |
| [ADR.md](docs/ADR.md) | реестр архитектурных решений (18 ADR: Контекст/Решение/Последствия) |
| [NAV-V2.md](docs/NAV-V2.md) | навигация v4: лендинг всегда на «/», 4 таба мини-апп, партнёрка 5+«Ещё» — решения и «как реализовано» |

Журнал работ (полная история волн и решений) — [PROGRESS.md](PROGRESS.md);
актуальный техдолг — [TECHDEBT.md](TECHDEBT.md).

## Тесты

```bash
uv run pytest -q                          # бэкенд (~1470, ~7 мин)
uv run pytest tests/infrastructure -q     # только воркеры/S3/источники
cd frontend && npx vitest run             # фронт (~550)
cd frontend && npm run build              # tsc -b + vite (проверка типов)
uv run ruff check app tests               # линт
```

Детали (фикстуры, Postgres-скипы, флэйк test_rag) — [docs/TESTING.md](docs/TESTING.md).

## Деплой

Прод — VPS + docker compose (профиль mono или micro) + Caddy с авто-
Let's Encrypt: пошаговый ранбук [deploy/DEPLOY.md](deploy/DEPLOY.md)
(bootstrap-скрипт, бэкапы, тест-стенд). Конфигурация окружения и
мониторинг — [docs/OPERATIONS.md](docs/OPERATIONS.md).

## Статус проекта

MVP в активной разработке; история волн, решений и живых прогонов —
[PROGRESS.md](PROGRESS.md) (новое сверху). Последняя крупная волна —
NAV-V2 навигация + AI-контур доверия (отчёты истории, качество объявлений,
выжимки) + резильентность S3-ингеста (2026-10-28). Открытые долги —
[TECHDEBT.md](TECHDEBT.md).

## Лицензия

Файла лицензии в репозитории нет — код распространяется как
проприетарный (все права за владельцем проекта).
