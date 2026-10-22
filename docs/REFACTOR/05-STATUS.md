# 05. Статус выполнения

**Дата:** 21.10.2026
**Ветка:** `develop` (HEAD `f83de19`)
**Фаза плана:** 0 — выполнена; фаза 1 — в работе

---

## 0. Обновление после подъёма Docker

Инфраструктура поднята из локальных образов (без скачивания):
Postgres 16.15, Redis 7.4.11, RabbitMQ 3.13.7 — все `healthy`,
alembic на `0018`. Детали — [07-INFRA.md](07-INFRA.md).

Добавлен тестовый harness (`tests/conftest.py`): `engine` на отдельной БД
`autoselect_test`, `session` в внешней транзакции с rollback после теста,
`client` (ASGITransport + подмена `get_session`), `internal_token`, `user`,
`auth_headers`, `auth_headers_user`, `auth_headers_admin`, `inactive_user`,
`tg_client`, `http`, `query_counter`/`assert_max_queries`.

| Метрика | Фаза 0 | После harness | После зачистки B/A/C |
|---|---|---|---|
| passed | 710 | 790 | **1076** |
| failed | 165 | 224 | **0** |
| errors | 181 | 46 | **0** |
| skipped | 0 | 0 | 3 (конкурентные, нужны 2 соединения) |

### Итог зачистки B/A/C (4 параллельные группы)

Все 30 красных файлов разобраны: 127 артефактов, 79 harness, 47 устаревших
API и 17 «реальных багов» — последние проверены по коду, часть оказалась
тест-артефактами, часть — настоящими дефектами.

**Исправлены дефекты продукта (не тесты):**

| Файл | Дефект |
|---|---|
| `app/db/session.py` | bare `raise` вне `except` → **500 на любом успешном запросе** |
| `app/api/filters.py` | `select(User.id)` при PK `telegram_id` → 500 на `POST /api/filters` |
| `app/adapters/kcar.py` | `carCd` как int → 0 объявлений; нет валидации id; `page_size=0` молча клампился |
| `app/core/pq_crypto.py` | seed 64B vs dk 2400B → PQ-шифрование не работало на `cryptography 50` |
| `app/schemas/partner.py` | `PartnerLead*Out` без `from_attributes` → 500; нет `partner_status` в «моих» |
| `app/services/partners.py` | нет auto-suspend/алерта; `balance_after` от устаревшего баланса |
| `app/adapters/che168.py` | цена без ×10⁴ (23.80 вместо 238000); `//evil.com` в `_safe_join`; карточка без URL терялась |
| `app/core/security.py` | канонический initData с percent-encoded `user` → 401 |
| `app/schemas/criteria.py` | нет границ года/цены/объёма и cross-field `min<=max` |
| `app/ai/agent.py` | `finalize_search` проверял полноту до `apply_patch` → отклонял валидный запрос |
| `app/api/dialog.py` | `sources` в finalize — dict вместо list (бот делал `join` по ключам) |
| `app/adapters/registry.py` | reload не закрывал заменённый адаптер (утечка httpx-клиента) |
| `app/schemas/lead.py` | `status` обязателен → ветка частичного PATCH мертва |
| `app/schemas/rag.py` | `confidence=null` → ValidationError |
| `app/adapters/encar.py` | `page_size=0` клампился; healthcheck путал «count-only» и «битый контракт» |
| `app/adapters/common/http.py` | `RemoteProtocolError` не ретраился (`NetworkError` ≠ `ProtocolError`) |
| `app/adapters/common/normalize.py` | «Мерседес Бенц» не нормализовался |
| `0010`/`0013` миграции | `alembic upgrade head` с нуля падал на именах PK |

### Исправленные дефекты продукта

| Файл | Дефект | Исправление |
|---|---|---|
| `app/adapters/kcar.py` | `carCd` приводился к int, а он строковый (`EC61405190`) → **0 объявлений при `total=8857`** | id читается как строка, пустой → `None` |
| `app/core/pq_crypto.py` | На `cryptography 50` `private_bytes_raw()` отдаёт 64-байтовый seed вместо `dk` 2400 → **PQ-шифрование не работало** | Поддержаны оба формата: seed (97B) и legacy full-dk (2433B) |
| `tests/test_kcar_adapter.py` | Ждал молчаливого отбрасывания неизвестной марки | Тест переписан: `FetchError` — осознанное решение, иначе выдача с чужими марками |

### Фаза 2: первый вертикальный срез

Добавлены чистые слои (без правки роутеров):

- `app/domain/shared` — `DomainError`, `DomainEvent`;
- `app/domain/favorites`, `app/domain/listings` — порты и ошибки;
- `app/application/shared` — `UnitOfWork`, `EventPublisher`, `Page`, `CursorCodec`;
- `app/application/favorites/use_cases.py` — `ToggleFavorite`, `ListFavorites`;
- `tests/domain/test_favorites_use_cases.py` — 6 тестов без БД (все зелёные).

Плюс фаза 1: `app/core/trace.py` (X-Request-ID + `trace_id` в логах) и
query-budget фикстуры.

### Фаза 3: инфраструктура избранного

- `app/infrastructure/db/outbox.py` — `OutboxMessage` + `OutboxDAO`;
- `app/infrastructure/db/uow.py` — `SqlAlchemyUnitOfWork` (данные + события
  одной транзакцией; rollback забывает события);
- `app/infrastructure/db/repositories/favorites.py` — атомарный toggle (CTE)
  и keyset-страница с первой ценой коррелированным подзапросом;
- `app/infrastructure/db/repositories/listings.py` — `exists_active`;
- миграция `0019_outbox` (upgrade/downgrade проверены на чистой БД);
- `tests/infrastructure/test_favorites_repository.py` — 4 интеграционных
  теста (toggle, keyset, outbox на commit, откат).

**Отдельный фикс:** `alembic upgrade head` с нуля падал на `0010`/`0013`
из-за имён PK (`sent_log_pkey` vs `pk_sent_log`). Сделано идемпотентно
(`DROP CONSTRAINT IF EXISTS` для обоих имён) — теперь цепочка
`upgrade head → downgrade -1 → upgrade` проходит без ошибок.

> Миграция `0019` в dev-БД пока не применена: продукт ещё не пишет в outbox.
> Применить перед подключением событий (фаза 6).

### Фаза 3: use case в роутере (за флагом)

- `use_case_favorites` (Settings, default `False`): toggle избранного
  идёт через `ToggleFavorite`, старый код остаётся веткой отката;
- `app/api/deps_use_cases.py` — переходный composition root на `Depends`
  (после Dishka заменяется на `FromDishka[...]`);
- `ListingNotFound` маппится в 404, контракт ответа не меняется;
- `tests/test_favorites_use_case.py` — включённый флаг, добавление/снятие,
  404 на несуществующем объявлении.

### Фаза 5: Dishka

Добавлены `dishka==1.10.1`, `dishka-faststream==0.7.0`, `faststream==0.7.7`.

- `app/di/providers/{settings,database,redis,repositories,use_cases,broker}.py`;
- `app/di/container.py::make_container(*extra)` (APP/REQUEST-скоупы);
- `use_dishka` (Settings, default `False`): в `app/main.py` контейнер
  поднимается и `setup_dishka` вызывается только при флаге; broker
  стартует/останавливается в lifespan;
- смоук `USE_DISHKA=1` → `/health` 200, `app.state.dishka_container` есть;
- `tests/di/test_container.py` — 3 теста (APP/REQUEST, реальные SQL поверх fake).

### Фаза 6: FastStream + outbox

- `app/infrastructure/messaging/topology.py` — exchange `tasks`, quorum-очереди
  `fetch`/`send`/`enrich` с `x-delivery-limit=5` и DLX;
- `publisher.py` — `FastStreamRawPublisher` и `BufferedEventPublisher`;
- `relay.py` — `OutboxRelay` с `FOR UPDATE SKIP LOCKED`;
- `app/main_worker.py` — воркер релея (`--once` для smoke);
- миграция `0019` применена к dev-БД; `relay_once published=0` на живом RabbitMQ;
- `tests/messaging/` — 16 тестов (топология, публикация, релей на реальном Postgres).

### Фаза 7 (срез): консьюмер `enrich`

- `app/transport/messaging/handlers/enrich.py` — `@broker.subscriber(ENRICH_QUEUE)`,
  `FromDishka[AsyncSession]`, Redis-идемпотентность;
- `app/main_consumer.py` — процесс-консьюмер, Dishka + FastStream;
- живой смоук на RabbitMQ: `Received → Processed`, повторный ключ →
  `enrich_skipped_duplicate`.

### Фаза 9 (срез): keyset-лента

- `GET /api/listings?cursor=…` — keyset без OFFSET/count;
- `next_cursor`/`has_more` аддитивны, старый контракт сохранён;
- `cursor` + сортировка кроме `new` → 422; 8 тестов.

### Текущий статус

```
1115 passed, 3 skipped, 0 failed, 0 errors
```

Все категории триажа закрыты. Остались архитектурные фазы 4/7–10:
хендлеры FastStream, перевод остальных роутеров на use case/Dishka,
keyset-пагинация ленты и `price_usd`, удаление legacy-кода.

---

## 1. Что было сломано и что исправлено (фаза 0)

В рабочем дереве было не два блокера, а девять: каждый следующий проявлялся
только после починки предыдущего.

| # | Файл | Проблема | Исправление |
|---|---|---|---|
| 1 | `app/api/dialog.py` | `RedisDep` импортировался из `app.core.deps`, где его нет | импорт из `app.core.deps_internal`, `SessionDep` оставлен из `deps` |
| 2 | `app/main.py` | второй `app = FastAPI(lifespan=...)` в конце файла затирал приложение с 67 маршрутами | `lifespan` перенесён в единственный конструктор `FastAPI` |
| 3 | `app/api/favorites.py` | `StaleDataError` импортировался из `sqlalchemy.exc` | `sqlalchemy.orm.exc` |
| 4 | `app/api/listings.py` | `_require_listing_active` потерял `async` → `SyntaxError: 'await' outside async function` | восстановлен `async def` |
| 5 | `app/core/deps_partner.py` | две версии модуля склеены в один файл, `from __future__` в середине → `SyntaxError` | оставлена новая версия; добавлен алиас `require_partner = get_partner_auth` для старых тестов |
| 6 | `app/ai/llm_client.py` | `dialog.py` импортировал `get_cached_llm_client`, которого не было | восстановлены `get_cached_llm_client()` (lru_cache) и `close_cached_llm_clients()` |
| 7 | `app/core/deps_internal.py` | `internal_token()` возвращал `SecretStr`; `SecretStr("")` truthy → fallback на `APP_SECRET_KEY` не работал, в заголовок уходил объект | разворачивание через `get_secret_value()` до проверки на пустоту |
| 8 | `app/bot/client.py`, `app/api/payments.py`, `app/bot/__main__.py`, `app/services/alerting.py`, `app/workers/send.py` | `SecretStr` попадал в HTTP-заголовки, URL и `Bot(token=...)` → `TypeError: Header value must be str` | `.get_secret_value()` в точках передачи |
| 9 | `tests/` | 4 файла-фрагмента без импортов/фикстур; 3 файла без `import pytest`; устаревший путь `app.adapters.common.http_client` | фрагменты вынесены в `tests/_drafts/*.py.disabled`; импорты исправлены |

---

## 2. Baseline: до и после

| Метрика | До | После |
|---|---|---|
| `import app.main` | `ImportError` | работает |
| Маршрутов в OpenAPI | 0 (приложение перекрыто) | **67** |
| Ошибок коллекции pytest | **22** | **0** |
| Собрано тестов | 773 | **1056** |
| passed | 704 | **710** |
| failed | 171 | **165** |
| errors (setup) | 181 | **181** |

Прогон: `.\.venv\Scripts\python.exe -m pytest -q --tb=no` (≈2 мин).

---

## 3. Исторический срез: почему тесты были красными (сейчас — 0)

Это не регрессии фазы 0 — это состояние тестового хозяйства.

### 3.1. 181 error: нет фикстур `client` / `session` / `app`

`tests/conftest.py` (и в HEAD) содержит только `fake_redis` и `fake_session`.
Фикстуры `client`, `session`, `engine`, `auth_headers` не определены нигде
централизованно, а тесты их требуют:

```
fixture 'client' not found
available fixtures: fake_redis, fake_session, ...
```

Часть файлов определяет `client` локально, часть — нет. Восстановить это
можно только вместе с тестовой БД: `session` — реальная `AsyncSession`,
без Postgres она бессмысленна.

### 3.2. 165 fail: смесь артефактов и реальных расхождений

| Причина | Пример | Вердикт |
|---|---|---|
| Тест-фрагменты без helper-функций | `test_encar.py`: `_criteria`, `_dummy_criteria`; `test_dedup.py`: `claim`, `_dedup_keys`, `asyncio` | переписать в фазе 1 |
| Тест ждёт удалённый публичный API | `partners.hash_key`, `partners.generate_api_key` — в WIP заменены на `hash_api_key`/`_new_raw_key` | тесты устарели; прод-код не использует |
| Тест-дубль Redis не умеет `eval` | `dedup.py` использует Lua, `FakeRedis.eval` нет | обновить fake |
| Фикстура возвращает tuple | `test_notifications_api.py`: `(client, session)` | переписать фикстуру |
| Нужна БД | `test_listings.py`, `test_me.py`, `test_metrics.py` | integration-маркер |

### 3.3. Чего нельзя проверить на этой машине

Docker Desktop не запущен (`docker ps` → pipe not found), Postgres/Redis/
RabbitMQ недоступны. Значит:

- интеграционные тесты (БД, очереди, миграции) не запускаются;
- `testcontainers`-сценарии из плана фазы 1 требуют запущенного Docker.

---

## 4. Что сделано с тестами

- `tests/_drafts/` — 4 файла-фрагмента (`test_auth`, `test_base`,
  `test_favorites`, `test_filters`) с расширением `.py.disabled`:
  не собираются, содержимое сохранено для переписывания.
- `import pytest` добавлен в `test_admin_api.py`, `test_dedup.py`,
  `test_proxy.py`.
- `tests/test_http_client.py`: импорт с `app.adapters.common.http_client`
  на существующий `app.adapters.common.http`.

---

## 5. Следующие шаги (фазы 4/7–10)

1. **Фаза 7**: хендлеры FastStream (`fetch`, `send`, `enrich`) с
   `FromDishka[...]` и идемпотентностью; cutover по одной очереди через
   теневые `*_v2` (план — `04-MIGRATION.md`).
2. **Фаза 5**: перевести остальные роутеры на `DishkaRoute` + `FromDishka`,
   убрать `app/api/deps_use_cases.py`.
3. **Фаза 4**: use cases для `listings`/`leads`/`auth`; убрать `SessionDep`
   из роутеров.
4. **Фаза 9**: keyset-пагинация ленты, `price_usd` + индексы, rate limit,
   `extra="forbid"` на входных схемах.
5. **Фаза 10**: удалить legacy `app/queue/rabbit.py`, `app/workers/*` после
   cutover; ADR и `docs/queues.md`.

---

## 6. Изменённые файлы (не закоммичено)

```
app/main.py
app/api/dialog.py
app/api/favorites.py
app/api/listings.py
app/core/deps_internal.py
app/core/deps_partner.py
app/ai/llm_client.py
app/bot/client.py
app/bot/__main__.py
app/api/payments.py
app/services/alerting.py
app/workers/send.py
tests/conftest.py (не менялся)
tests/test_admin_api.py
tests/test_dedup.py
tests/test_proxy.py
tests/test_http_client.py
tests/_drafts/ (новые)
docs/REFACTOR/*.md
```

Коммит не делал: в дереве остаются ваши незакоммиченные правки (38 файлов).
