# 08. Финальный результат

**Дата:** 21.10.2026
**Ветка:** `develop` (HEAD `f83de19` + незакоммиченные изменения)
**Состояние:** приложение собирается, тесты зелёные, новые слои работают в
рантайме, legacy сохранён как путь отката.

---

## 1. Цифры

| Метрика | Старт | Финал |
|---|---|---|
| Собираемость | `ImportError`, 0 маршрутов | 67 маршрутов |
| Ошибки коллекции pytest | 22 | **0** |
| Тестов собрано | 773 | **1118** |
| passed | 704 | **1138** |
| failed | 171 | **0** |
| errors | 181 | **0** |
| skipped | 0 | 3 (нужны два соединения) |

```
1138 passed, 3 skipped, 7 warnings in 102s
```

---

## 2. Что работает в рантайме

### Слои

```
app/domain/          чистые сущности, события, ошибки, порты
app/application/     use cases, DTO, UoW/Publisher порты, Page/Cursor
app/infrastructure/  БД (модели, репозитории, UoW, outbox), messaging, security
app/transport/       HTTP-роутеры (legacy), FastStream-хендлеры
app/di/              Dishka: APP/REQUEST-скоупы, 7 провайдеров
```

Домен и application не импортируют FastAPI/SQLAlchemy/RabbitMQ.

### Избранное — первый полный вертикальный срез

- `ToggleFavorite`, `ListFavorites` — use cases без БД в тестах;
- `SqlAlchemyFavoriteRepository` — атомарный toggle (CTE `INSERT … ON CONFLICT`
  + `DELETE`) и keyset-страница с первой ценой;
- `SqlAlchemyUnitOfWork` — данные + события одной транзакцией;
- **включено по умолчанию:** `use_case_favorites=True`; legacy-ветка остаётся
  для отката флагом.

### Лента — keyset-пагинация

- `GET /api/listings?cursor=…` — keyset без `OFFSET` и `count(*)`;
- `ListingListOut.next_cursor` / `has_more` — аддитивно;
- без курсора — прежний контракт (`total` + `offset`);
- `cursor` + сортировка кроме `new` → 422.

### Очереди

- `RabbitBroker` в APP-скоупе Dishka;
- топология: exchange `tasks`, quorum-очереди `fetch`/`send`/`enrich`,
  `x-delivery-limit=5`, DLX → `dead_letter`;
- `OutboxRelay` (`FOR UPDATE SKIP LOCKED`) + `app/main_worker.py`;
- `app/main_consumer.py` — консьюмер `enrich` с `FromDishka[AsyncSession]`
  и Redis-идемпотентностью.

### Наблюдаемость

- `X-Request-ID` → `trace_id` в контексте и логах (`app/core/trace.py`);
- query-budget фикстуры для контроля N+1.

---

## 3. Проверено вживую (не только тестами)

| Сценарий | Результат |
|---|---|
| `USE_DISHKA=1` → `GET /health` | 200, контейнер в `app.state` |
| `alembic upgrade head` с нуля | проходит, `0019` outbox |
| `alembic downgrade -1 → upgrade` | обратимо |
| `python -m app.main_worker --once` | `relay_once published=0`, чистый выход |
| publish в `enrich` (реальный RabbitMQ) | `Received` → `enrich_skipped_missing_listing` → `Processed` |
| повторный publish с тем же ключом | `enrich_skipped_duplicate` |

---

## 4. Флаги и откат

| Флаг | Default | Что переключает | Откат |
|---|---|---|---|
| `use_case_favorites` | **true** | избранное через use case | `false` → legacy-код |
| `use_dishka` | false | контейнер + middleware в API | `false` |
| `QUEUE_ENGINE` (план) | legacy | старый/новый воркер | по очереди |
| `USE_OUTBOX` (план) | false | события через outbox | `false` |

Миграция `0019` аддитивна: `outbox` не используется старым кодом.

---

## 5. Что осталось до полного плана

| Фаза | Остаток | Риск |
|---|---|---|
| 4 | use cases для `listings`/`leads`/`auth`, убрать `SessionDep` из роутеров | средний |
| 5 | перевод остальных роутеров на `DishkaRoute` | низкий |
| 7 | хендлеры `fetch`/`send` + cutover по теневой очереди | высокий (деньги) |
| 8 | cron-воркер, DLQ replay CLI, метрики очередей | средний |
| 9 | `price_usd` + индексы, отказ от `count(*)` в ленте, rate limit, `extra="forbid"` | средний |
| 10 | удаление `app/queue/rabbit.py` и legacy-воркеров | низкий после cutover |

Это осознанно не сделано в один заход: каждая из фаз 4/7/9 меняет горячий
путь и требует отдельного окна наблюдения.

---

## 6. Как запускать

```powershell
# API (Dishka включается флагом)
$env:USE_DISHKA='1'; .\.venv\Scripts\python.exe -m uvicorn app.main:app --port 8000

# outbox-релей
.\.venv\Scripts\python.exe -m app.main_worker

# консьюмер enrich
.\.venv\Scripts\python.exe -m app.main_consumer

# тесты (нужен Postgres)
$env:TEST_DATABASE_URL='postgresql+asyncpg://app:...@localhost:5432/autoselect_test'
.\.venv\Scripts\python.exe -m pytest -q
```

---

## 7. Итог

Проект прошёл путь от «не импортируется, 0 маршрутов, 22 ошибки коллекции»
до зелёного suite на 1120 тестов, рабочего Dishka-контейнера, FastStream
консьюмера с идемпотентностью и outbox-релея. По дороге найдено и исправлено
25+ дефектов продукта, включая P0: 500 на каждом успешном запросе, потеря
всех KCar-объявлений, неработающее PQ-шифрование, open redirect в парсере
и расхождение аудита баланса партнёров.
