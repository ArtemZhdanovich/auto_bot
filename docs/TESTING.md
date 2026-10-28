# Тесты

## Запуск

```bash
uv run pytest -q                      # весь бэкенд (~1470, ~7 мин)
uv run pytest tests/catalog -q        # контур каталога/рынка/чатов
uv run pytest tests/infrastructure -q # воркеры, S3-зеркало, источники
cd frontend && npx vitest run         # фронт (~547)
cd frontend && npm run build          # tsc -b + vite (типы)
uv run ruff check app tests          # линт
```

Postgres должен быть поднят (docker или локально; тесты сессии
пропускаются с skip, если БД недоступна). Известный флейк:
`tests/dialog/test_rag.py::test_answer_question_without_rag_uses_llm_answer`
— ходит в живой RAG-сервис; изолированно зелёный (см. RUNBOOK).

## Структура

- `tests/catalog` — API-контур: listings/analysis/similar/market
  (lifecycle, пакеты, quality)/chats (groups, governance, операторы,
  личный канал, summary)/history_report/description.
- `tests/transport` — gateway-маршруты, мини-апп API, notifications.
- `tests/infrastructure` — fetch/send воркеры (дайджесты, экспирация
  размещений, S3-зеркало), адаптеры encar/kcar, link-preview (SSRF).
- `tests/partners`, `tests/billing`, `tests/bot`, `tests/dialog`,
  `tests/identity`, `tests/di`, `tests/architecture`, `tests/messaging`,
  `tests/application`, `tests/domain`, `tests/security`.
- Фикстуры: `tests/conftest.py` — FakeSession/FakeRedis, test-контейнер
  Dishka (install_test_container), фабрики users/listings; автous-фикстура
  фиксирует партнёрский sampling вне тестов сэмплинга.

## Соглашения

Транспорт тонкий: логика в application-сценариях, их и тестируем
фабриками-фейками (порты подменяются через extra-провайдеры dishka).
SQL-зависимое — сквозные через test-клиент ASGI на реальном PG.
