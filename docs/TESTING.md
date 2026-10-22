# Тесты

## Запуск

```bash
uv run pytest -q                 # все тесты (без test-БД: passed + skipped)
uv run pytest tests/partners -q  # один пакет
uv run ruff check app tests
uv run mypy app
cd frontend && npm run build     # tsc --noEmit + vite build + vitest
```

Часть интеграционных тестов требует тестовой БД/Redis — без них они
скипаются (ожидаемо ~250 skip).

## Структура (зеркало архитектуры)

| Пакет | Что тестирует |
|---|---|
| `tests/identity/` | auth API, guest-сценарии, онбординг, /me, security initData |
| `tests/catalog/` | лента/keyset-пагинация, избранное, analysis API, AI-анализ цены, перевод |
| `tests/billing/` | платежи Stars API |
| `tests/partners/` | партнёрский контур, CPL-биллинг, лиды (+ `helpers.py` — сборка UoW+репозиторий+юзкейс) |
| `tests/dialog/` | dialog API, хранилище диалогов, агент, RAG-клиент, tools, AI-поиск |
| `tests/admin/` | admin API, ассистент, аудит, метрики, мониторинг, аналитика, воронки, ParserDoctor, реестр |
| `tests/bot/` | хендлеры бота: анализ, калькулятор, dialog UI, inline-share, throttle |
| `tests/security/` | prod-секреты (fail-fast), пустой internal-токен, PQ-крипто, token bucket |
| `tests/infrastructure/` | адаптеры encar/kcar/che168, normalize/dedup, fetch/send/delivery/notify, storage (S3), scheduler/cron, heartbeat, LLM/http клиенты, proxy, trace |
| `tests/transport/` | gateway routing/proxy, miniapp API, notifications API, calc API, enrich-хендлер |
| `tests/architecture/` | правила слоёв, смоук всех микросервисов (границы доменов, OpenAPI-паритет) |
| `tests/application/` | ingest-пайплайн, quota use cases |
| `tests/domain/` | чистая логика избранного |
| `tests/infrastructure/` (репозитории) | favorites repository |
| `tests/di/` | dishka-контейнер (разрешение графа) |
| `tests/messaging/` | publisher, outbox-релей, топология |

Хелперы уровня проекта — `tests/conftest.py` (см. ниже).

## Фикстуры и стабы (`tests/conftest.py`)

- `FakeSession` — строковые PK, rollback, `get`, `executed_params`;
- `FakeRedis` — `get/set/setex/delete` + `ttls` (дедуп, диалоги);
- `MockLLMClient` — `script`, `.calls`;
- `session` — живая тестовая БД (для сервисных инвариантов: атомарный claim и
  т.п. — на FakeSession невоспроизводимо);
- ASGI-клиент (`httpx.AsyncClient` + `ASGITransport`) с реальной сессией и
  FakeRedis; dishka-контейнер с переопределениями.

## Конвенции

- Тест-модули уникальны по имени файла (importmode=prepend, без `__init__.py`).
- Внутрипакетные хелперы импортируются как
  `from tests.<package>.helpers import …` (pythonpath = корень репо).
- Архитектурные инварианты — в `tests/architecture/test_layers.py`: слои не
  нарушают зависимости, порты — `Protocol`.
