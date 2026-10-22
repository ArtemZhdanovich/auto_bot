# bot-encar — Экосистема подбора автомобилей (Encar + KCar + Che168)

Telegram-бот + Mini App для подбора автомобилей из Кореи и Китая: агрегация
объявлений с нескольких источников, AI-диалоговый ассистент (GLM) с RAG-базой
знаний, уведомления по фильтрам, монетизация (Telegram Stars + CPL-лиды для
партнёров), партнёрский API и полный админ-контур.

- **Языки/рантайм**: Python 3.13 (asyncio), TypeScript/React 18
- **Менеджер пакетов**: `uv` (Python), `npm` (frontend)
- **БД/инфраструктура**: PostgreSQL 16, Redis 7, RabbitMQ 3, Qdrant, MinIO (S3)
- **Качество**: `uv run pytest -q` — зелёные, `ruff` clean, `mypy` настроен

## Возможности

- **Агрегация**: encar.com, kcar.com, che168.com — адаптеры с конфигом в БД,
  дедупликация, история цен, сырые ответы в MinIO, прокси для гео-блокировок.
- **AI-подбор**: диалоговый агент (GLM) извлекает критерии поиска, ищет по БД,
  отвечает с кнопками; RAG-база знаний (гибридный поиск) — отдельный
  микросервис; перевод тайтлов (NLLB) — отдельный микросервис.
- **Мини-приложение**: лента, карточка, фильтры, избранное, AI-поиск, профиль;
  авторизация через Telegram initData.
- **Монетизация**: подписки за Telegram Stars (Free/Pro/Business — лимиты
  фильтров), лиды с AI-квалификацией, партнёрский CPL-контур с балансами.
- **Админка**: источники и их health без деплоя, ручной парсинг, лиды,
  воронки/retention, аудит действий, AI-ассистент, ParserDoctor (авторемонт
  селекторов), метрики и алерты в Telegram.

## Архитектура (кратко)

```
                ┌──────────────────── Telegram ────────────────────┐
                │  Bot API (long polling)     WebApp (Mini App)    │
                └────────┬─────────────────────────┬───────────────┘
                    ┌───▼────┐   enqueue   ┌──────▼───────┐
                    │  bot   │────────────▶│  Mini App    │ React 18 + nginx
                    └───┬────┘             └──────┬───────┘
                        │                         │ REST (JWT)
┌──────────────┐   ┌────▼─────────────────────────▼───────┐
│ encar/kcar/  │   │   gateway :8010 → 6 доменных        │
│ che168 ──────┼──▶│   сервисов (identity, catalog,       │
│ (адаптеры,   │   │   billing, partners, dialog, admin)  │
│  прокси)     │   │   = один код app/ в двух профилях    │
└──────────────┘   └──┬──────┬───────┬───────┬──────┬─────┘
                   PG 16  Redis 7  Rabbit 3  LLM   services/rag + translate
```

Подробно — [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Быстрый старт

```bash
cp .env.example .env          # заполнить BOT_TOKEN, APP_SECRET_KEY, DSN
uv run alembic upgrade head   # миграции
uv run uvicorn app.main_api:app --port 8000        # backend (mono)
uv run python -m app.bot                            # бот (другой терминал)
uv run python -m app.infrastructure.workers.fetch   # воркеры (ещё два)
uv run python -m app.infrastructure.workers.send
cd frontend && npm run dev     # Mini App :8080 (ещё один)
```

Docker Compose (вся инфраструктура + mono/micro профили) и полный набор
конфигурации — [docs/OPERATIONS.md](docs/OPERATIONS.md).

## Документация

| Документ | Что внутри |
|---|---|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | слои, 6 прикладных сервисов (ports/dto), DI, очередь, микросервисы, БД |
| [docs/API.md](docs/API.md) | HTTP API: аутентификация, все эндпоинты |
| [docs/SOURCES.md](docs/SOURCES.md) | адаптеры источников, парсинг, дедуп, воркеры, raw_payload |
| [docs/AI.md](docs/AI.md) | LLM-клиенты, агент, RAG-микросервис, перевод |
| [docs/MONETIZATION.md](docs/MONETIZATION.md) | подписки Stars, лимиты тарифов, лиды, партнёрский CPL-контур |
| [docs/OPERATIONS.md](docs/OPERATIONS.md) | запуск, docker compose, конфиг (.env), мониторинг, прод-чеклист |
| [docs/TESTING.md](docs/TESTING.md) | структура тестов, фикстуры, как запускать |
| [docs/SECURITY.md](docs/SECURITY.md) | модель безопасности; журнал аудита — [INCIDENTS.md](INCIDENTS.md) |
| [docs/UIUX_TZ.md](docs/UIUX_TZ.md) | UI/UX-ТЗ: дизайн-система, Mini App, админка |

Журнал работ и статус — [PROGRESS.md](PROGRESS.md) (статус-дашборд в §0),
план фаз — [PLAN.md](PLAN.md), техдолг — [TECHDEBT.md](TECHDEBT.md),
проектные документы рефакторинга — [docs/REFACTOR/](docs/REFACTOR/README.md).
