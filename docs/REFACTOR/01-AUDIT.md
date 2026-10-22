# 01. Аудит текущего состояния

**Дата:** 21.10.2026
**Репозиторий:** `auto_bot` (`bot-encar`), ветка `master`, HEAD `f83de19`
**Рабочее дерево:** 38 изменённых файлов, `+7253 / −1360` строк (незакоммичено)
**Метод:** чтение кода, подсчёт по AST/регэкспам, `pytest --collect-only`,
сверка утверждений внешнего AI-ревью с текущими файлами.

> **Обновление 21.10.2026:** все четыре блокера (B1–B4) исправлены на ветке
> `develop`. Актуальные цифры и список правок — в [05-STATUS.md](05-STATUS.md).
> Раздел 2 сохранён как история дефектов и обоснование фазы 0.

---

## 1. Метрики

| Показатель | Значение |
|---|---|
| Python-файлов в `app/` | 111 |
| Строк в `app/` | 22 089 |
| Маршрутов (`@router.*` / `@app.*`) | ≈68 |
| `response_model=` | 51 |
| `APIRouter(...)` | 18 |
| `session.commit()` в `app/` | 44 |
| `session_factory()` (сессия создаётся в обход DI) | 17 |
| `text(...)` (raw SQL) | 49 вхождений |
| `relationship()` в моделях | **0** |
| `lazy=` | **0** |
| `extra="forbid"` | **0** |
| Тест-файлов | 66 |
| Уникальных имён тестов | 943 |
| Собрано pytest | 773 теста, **22 ошибки коллекции** |
| Дублирующихся имён тестов | 26 (52 вхождения) |
| Сервисов в `docker-compose.yml` | 27 (моно + 6 микросервисов + RAG + gateway) |

Распределение по модулям:

| Модуль | Строк | Роль сегодня | Проблема |
|---|---|---|---|
| `app/api` | 4 933 | HTTP-роутеры | бизнес-логика, SQL, `commit()`, доменные ошибки через `HTTPException` |
| `app/services` | 4 030 | «сервисы» | смешаны доменные правила и I/O; создают сессии сами |
| `app/adapters` | 3 466 | источники данных | хорошая изоляция; реестр глобальный, клиенты создаются на вызов |
| `app/ai` | 2 120 | LLM/RAG/агент | инфраструктура и сценарий в одном слое |
| `app/bot` | 1 959 | aiogram | транспорт; `BackendClient` создаёт `AsyncClient` на каждый запрос |
| `app/db` | 1 845 | модели, сессии | нет связей, нет репозиториев, глобальный engine |
| `app/core` | 1 437 | конфиг, JWT, deps | три механизма аутентификации в разных файлах |
| `app/workers` | 882 | консьюмеры | `ctx: dict` как service locator |
| `app/schemas` | 868 | Pydantic | часть — DTO транспорта, часть — доменные контракты |
| `app/queue` | 409 | свой `TaskWorker` | дублирует фреймворк, нет идемпотентности и shutdown |

---

## 2. Блокеры: дерево сейчас не работает

### B1. Второй `FastAPI()` затирает приложение со всеми роутерами

Проявится сразу после устранения B2 (пока приложение не импортируется, до
этой строки дело не доходит).

`app/main.py`, последние строки (незакоммиченное изменение):

```python
app.include_router(partners.admin_router)
app.include_router(partners.router)

from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.db.session import warmup_engine, dispose_engine

@asynccontextmanager
async def lifespan(app: FastAPI):
    await warmup_engine()
    yield
    await dispose_engine()

app = FastAPI(lifespan=lifespan)   # ← перекрывает app, созданный выше
```

Итог: финальный объект `app` не содержит ни одного из 18 подключённых
роутеров. Наружу остаются только служебные `/openapi.json`, `/docs`,
`/redoc` — то есть ~4 маршрута вместо ~68. `git diff` подтверждает, что блок
добавлен поверх рабочей версии.

**Фикс:** перенести `lifespan` в первый конструктор:

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    await warmup_engine()
    yield
    await dispose_engine()

app = FastAPI(title=..., version=..., description=..., lifespan=lifespan)
```

### B2. `ImportError` при импорте приложения

```
app/api/dialog.py:26: from app.core.deps import RedisDep, SessionDep
E ImportError: cannot import name 'RedisDep' from 'app.core.deps'
```

`RedisDep` живёт в `app/core/deps_internal.py`. Из-за этого не импортируется
`app.main`, и падают 22 тест-модуля каскадом (traceback одинаковый).

**Фикс:** `from app.core.deps_internal import RedisDep`.

### B3. Тест-фрагменты без импортов

7 файлов используют `pytest.` без `import pytest`:

```
test_admin_api.py, test_auth.py, test_base.py, test_dedup.py,
test_favorites.py, test_filters.py, test_proxy.py
```

`tests/test_favorites.py` начинается так:

```python
# tests/api/test_favorites.py

# --- list_favorites: пагинация ------------------------------------

@pytest.mark.asyncio
async def test_list_favorites_paginated(client, auth_headers, many_favorites):
```

Ни импортов, ни фикстур `client`, `auth_headers`, `many_favorites` в
`conftest.py` нет (там только `FakeRedis`, `FakeSession`). Это вставки из
внешнего чата, а не рабочие тесты.

### B4. Следствие: «зелёные тесты» недостоверны

`pytest --collect-only` даёт 773 собранных теста и 22 ошибки. Любой CI с
`-x`/`--strict` красный. Пока это не исправлено, измерять качество
рефакторинга нечем.

---

## 3. Карта нарушений слоёв

| Слой | Файлы | Что нарушено | Следствие |
|---|---|---|---|
| Транспорт | `app/api/*.py` | SQL-запросы, `commit()`, проверки владения, бизнес-правила | нельзя переиспользовать логику в боте/воркерах; тесты требуют HTTP |
| Транспорт | `app/api/listings.py` | `enrich_listing_specs()` (сетевой вызов + запись) внутри `GET` | латентность 5–30 с на PDP, запись в read-path |
| Приложение | отсутствует | нет use case-ов; сценарии размазаны по роутерам и сервисам | нет изолированных тестов без БД |
| Домен | отсутствует | правила живут в `services/*` и `api/*`; сущности = ORM-модели | домен нельзя протестировать без SQLAlchemy |
| Инфраструктура | `app/services/*` | создают сессии через `session_factory()`, коммитят | границы транзакций неявные, 17 точек входа в БД |
| Инфраструктура | `app/db/models.py` | 0 `relationship()`, нет мапперов в домен | ручные JOIN-ы, домен зависит от схемы таблиц |
| Инфраструктура | `app/adapters/registry.py` | глобальный реестр, конфигурируется императивно | нельзя подменить в тесте без monkeypatch |
| Очереди | `app/queue/rabbit.py` | свой worker: нет идемпотентности, visibility timeout, replay, дренажа | повторная доставка = повторный `charge_lead` |
| Очереди | `app/workers/*` | `ctx: dict` вместо DI; `enqueue_task` внутри бизнес-операций до commit | событие может уйти при откате транзакции |
| Конфигурация | `app/core/deps*.py` | три механизма аутентификации (JWT, internal token, partner key) в разных местах | дублирование, разные форматы ошибок |
| Бот | `app/bot/client.py` | новый `httpx.AsyncClient` на каждый вызов | нет пула соединений, лишние TLS-handshake |

---

## 4. Сверка с внешним AI-ревью (ссылка DeepSeek)

Чат содержит 98 сообщений и ~480 заголовков-«находок» по 41 файлу. Ревью
полезно как карта поверхностей, но **сделано по снимку до текущих фиксов**.
Ниже — проверка по коду, а не по тексту ревью.

### 4.1. Уже исправлено (повторно не трогать)

| Утверждение ревью | Фактическое состояние | Доказательство |
|---|---|---|
| `deps_internal.py`: пустой токен = валидный токен | fail-closed `503` при пустом секрете; пустой заголовок → `401` до сравнения; сравнение в байтах | `app/core/deps_internal.py:24-63` |
| `compare_digest` падает на non-ASCII | `_as_bytes(...).encode("utf-8", "surrogatepass")` + `except TypeError` | там же, `_as_bytes` |
| Redis без таймаутов/shutdown | `socket_timeout=5`, `socket_connect_timeout=5`, `health_check_interval=30` | `get_redis()` |
| `favorites.list_favorites` без пагинации | `limit 1..100`, `offset ≥ 0`, `limit/offset` в запросе | `app/api/favorites.py:36-60` |
| История цен грузится целиком | `DISTINCT ON (listing_id)` одним запросом | `app/api/favorites.py:86-102` |
| Мутация Pydantic-модели `first_price` | `model_copy(update=...)` | `app/api/favorites.py:107-110` |
| Toggle: read-then-write, 500 на гонке | `INSERT ... ON CONFLICT DO NOTHING` + `DELETE` по `rowcount` + `except IntegrityError` + `StaleDataError` | `app/api/favorites.py:150-260` |
| Нет проверки `is_active` | `403` в `toggle_favorite` | `app/api/favorites.py:139-143` |
| `leads`: дубли при ретраях | `Idempotency-Key` + повторный `select` после `IntegrityError` | `app/api/leads.py:60-140` |
| `partners`: lost update баланса | `UPDATE ... balance = balance ± :x RETURNING balance`; `claim_batch` — `FOR UPDATE SKIP LOCKED` | `app/services/partners.py` |
| JWT: refresh проходит как access | `decode_access_token` проверяет `type == "access"`; `require: exp, iat, sub, type`; leeway 10 c | `app/core/security.py` |
| Секреты в открытом виде | `SecretStr` + прод-валидация длины и обязательности `INTERNAL_TOKEN`; TTL-инвариант | `app/core/config.py` |

**Вывод:** существенная часть «критичных» пунктов ревью уже закрыта в
рабочем дереве — по проверенным пунктам не менее трети. Применять его как
патч-лист нельзя: часть замечаний описывает код, которого больше нет.

### 4.2. Подтверждается в текущем коде (берём в работу)

| Находка | Где | Подтверждение |
|---|---|---|
| Бизнес-логика и SQL в роутерах | `app/api/*.py` | 4 933 строки, 44 `commit()`, 49 `text()` |
| Нет DI-контейнера, глобальные синглтоны | `app/db/session.py`, `app/queue/rabbit.py`, `app/core/deps_internal.py` | `engine` на импорте, `_publisher`, `@lru_cache get_redis` |
| Нет идемпотентности задач | `app/queue/rabbit.py` | `decode_message` не содержит `idempotency_key`; `_process` повторяет handler 3 раза |
| Нет дренажа in-flight при shutdown | `app/queue/rabbit.py` | `stop()` только ставит `Event`; `run()` закрывает соединение сразу |
| Нет replay DLQ | `app/queue/rabbit.py` | DLQ только читается для алерта, CLI отсутствует |
| Нет типизации сообщений | `app/queue/rabbit.py` | `args: tuple`, `kwargs: dict`, `job: str` |
| `OFFSET`-пагинация | `app/api/listings.py`, `app/api/favorites.py` | 6 вхождений `.offset(` |
| `count(*)` на каждый запрос ленты | `app/api/listings.py:224-228` | отдельный `select(func.count())` |
| Курс валют в SQL-выражении | `app/api/listings.py:76-81` | `case(... Listing.price / 1350.0 ...)` → индекс по `price` не работает |
| Запись в read-path | `app/api/listings.py:386` | `await enrich_listing_specs(session, listing)` внутри `GET` |
| `AsyncClient` на каждый вызов | `app/bot/client.py` | три `async with httpx.AsyncClient(...)` |
| Входные схемы без `extra="forbid"` | `app/schemas` | 0 вхождений |
| Нет rate limit на HTTP API | `app/main.py` | middleware отсутствует; лимиты есть только для Telegram и партнёрского API |

### 4.3. Требует проверки на стенде, не берём на веру

- Тайминговые атаки на `auth_login` (user enumeration) — нужен замер, а не
  рассуждение.
- «Модуло-биас» `_synthetic_telegram_id` — зависит от фактической реализации;
  в текущем виде не воспроизведено.
- Оценки «50 000 строк в память» и «в 19 раз медленнее» — без `EXPLAIN
  (ANALYZE, BUFFERS)` на реальных данных это гипотезы.

---

## 5. Очереди: факты

`app/queue/rabbit.py` — 409 строк, заменяет ARQ и одновременно решает пять
задач: публикация, консьюминг, cron, interval, мониторинг DLQ.

| Свойство | Сейчас | Риск |
|---|---|---|
| Топология | exchange `tasks` (direct), `fetch`, `send`, `tasks.dlx` → `dead_letter` | рабочая, сохраняем |
| Формат сообщения | `{"id", "job", "args", "kwargs"}` без схемы | ошибка сигнатуры = runtime |
| Повторы | 3 попытки, backoff `2 × 2^n` внутри процесса | при падении процесса попытка теряется |
| Идемпотентность | нет | повторная доставка = повторное списание |
| Ack/nack | `message.process(ignore_processed=True)` + ручной `nack` | двойной ack/nack |
| Visibility timeout | нет | «зависшая» задача не переотдаётся |
| DLQ replay | нет | очередь растёт, разобрать нельзя |
| Graceful shutdown | `stop()` не ждёт in-flight | потеря задач при деплое |
| Cron | свой цикл + Redis-локи | рестарт Redis → повторный прогон |
| Приоритеты | нет | digest конкурирует с instant |

Это ровно этап 13 из `TECHDEBT.md`. Предлагаемое решение (FastStream +
идемпотентность + outbox) закрывает 13.1–13.14, а не добавляет ещё один
самописный слой.

---

## 6. Тесты: дубли и структура

26 имён тестов встречаются более одного раза. Основные пары:

| Дубли | Файлы | Что делать |
|---|---|---|
| `test_fetch_maps_to_unified_listing`, `test_fetch_skips_items_without_id`, `test_healthcheck_false_on_500`, `test_fetch_raises_fetch_error_on_http_error` | `test_encar_adapter.py` ↔ `test_kcar_adapter.py` | параметризовать по адаптеру |
| `test_map_item_*`, `test_unknown_make_raises`, `test_eur_currency_raises` | `test_encar.py` ↔ `test_kcar.py` | вынести в общий `test_normalize.py` |
| `test_message_text_empty_rejected`, `test_message_text_too_long_rejected`, `test_finalize_invalid_criteria_rejected` | `test_ai_search.py` ↔ `test_dialog_api.py` | оставить в контрактных тестах диалога |
| `test_access_token_rejected_as_refresh` | `test_auth_api.py` ↔ `test_security_initdata.py` | оставить в `test_security_initdata.py` |
| `test_by_url_found` | `test_listings.py` ↔ `test_miniapp_api.py` | оставить в `test_listings.py` |
| `test_onboarded_idempotent` | `test_me.py` ↔ `test_onboarding.py` | объединить в `test_onboarding.py` |
| `test_metrics_requires_internal_token` | `test_metrics.py` ↔ `test_monitoring.py` | оставить в `test_metrics.py` |

Отдельно: `test_encar.py` / `test_encar_adapter.py` и `test_kcar.py` /
`test_kcar_adapter.py` — не дубли по смыслу, а два уровня (нормализация и
HTTP-адаптер), но имена тестов совпадают и мешают точечному запуску.

**Приоритет:** сначала вернуть собираемость (B3), потом дедуп — иначе
непонятно, что именно дублируется.

---

## 7. Инфраструктура и деплой

`docker-compose.yml` содержит 27 сервисов: монолит (`backend`, `bot`,
`worker_fetch`, `worker_send`), шесть микросервисов (`identity`, `catalog`,
`billing`, `partners`, `dialog`, `admin`), `gateway`, RAG (`rag_api`,
`rag_worker`, `rag_migrate`), `translate`, плюс Postgres, Redis, RabbitMQ,
Qdrant, MinIO.

Для миграции это означает:

- **переключать очередь можно только по одной**: `fetch` и `send` — разные
  процессы, их можно катить независимо;
- **схему БД менять только аддитивно** (expand/contract): микросервисы
  используют те же таблицы;
- **`gateway` даёт точку для blue/green** и для rate limit на периметре;
- **единый контейнер Dishka** переиспользуется в `backend`, `bot` и
  воркерах, но собирается разными фабриками (`make_api_container`,
  `make_bot_container`, `make_worker_container`).

---

## 8. Выводы

1. Дерево в состоянии «незаконченная правка»: 2 блокера импорта/маршрутов и
   22 несобирающихся тест-модуля. Любой рефакторинг до их устранения —
   движение вслепую.
2. Архитектурная проблема не в отдельных багах, а в отсутствии границ:
   транспорт знает про SQL, домен — про ORM, очередь — про бизнес-операции.
3. Внешнее ревью полезно как чек-лист поверхностей, но его выводы нельзя
   применять без сверки: значительная часть уже исправлена, часть —
   предположения без замеров.
4. Техдолг уже структурирован (`TECHDEBT.md`, этапы 13–14). Новый пакет
   документов не заменяет его, а даёт архитектурную рамку, в которую эти
   этапы укладываются.
