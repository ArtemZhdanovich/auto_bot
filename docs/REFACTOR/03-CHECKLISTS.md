# 03. Чек-листы: безопасность, производительность, тесты

Статусы: ✅ сделано и проверено в текущем дереве · ⚠️ частично / требует
замера · ❌ отсутствует · n/a — неприменимо к текущей схеме.

---

## 1. Безопасность

### 1.1. Доступ и владение

| # | Требование | Сейчас | Мера | Критерий приёмки |
|---|---|---|---|---|
| S1 | IDOR: владение ресурсом проверяется в use case, а не в роутере | ⚠️ проверки размазаны: `favorites` — по `user_id` в SQL, `partners` — в сервисе, `dialog` — нужно проверить владельца сессии | `GetLeadForPartner`, `GetDialog` и т.п. проверяют `owner_id` внутри application; при чужом ресурсе — `NotFound` (404), чтобы не раскрывать существование | `test_partner_cannot_read_foreign_lead` → 404; `test_dialog_foreign_session_404` |
| S2 | Аутентификация: access ≠ refresh | ✅ `decode_access_token` проверяет `type == "access"` | сохранить; тест на refresh-как-access | уже есть в `test_security_initdata.py` |
| S3 | Баны: деактивированный пользователь не пишет | ⚠️ есть в `toggle_favorite`, нет системной гарантии | `CurrentUser.is_active` проверяется в одном месте (провайдер) + 403 | `test_deactivated_user_cannot_write` для 3 эндпоинтов |
| S4 | Внутренний API: пустой токен = отказ | ✅ fail-closed 503 + 401 до сравнения | сохранить | `test_internal_token_empty_rejected` |
| S5 | Партнёрские ключи: хранение хеша, ротация, отзыв | ✅ `key_hash`, `rotate_key`, `revoke_keys` | сохранить; добавить `last_used_at` в аудит | есть `test_partners.py` |
| S6 | Сессия Telegram-логина привязана к браузеру | ⚠️ внешнее ревью указывает на выдачу JWT по коду; нужно проверить сценарий session fixation | одноразовый код + `state` + привязка к browser-bound cookie/`device_id`; TTL ≤ 2 мин | `test_login_code_single_use`, `test_login_code_bound_to_browser` |

### 1.2. Данные и сериализация

| # | Требование | Сейчас | Мера | Критерий |
|---|---|---|---|---|
| S7 | ORM-модели не уходят в ответ | ⚠️ 51 `response_model` на ~68 маршрутов; `ListingOut.model_validate(ORM)` | явные output-схемы + маппер `entity → schema`; ORM не покидает infrastructure | `test_no_orm_in_response` (обход OpenAPI-схем) |
| S8 | Входные схемы с `extra="forbid"` | ❌ 0 вхождений | `ConfigDict(extra="forbid")` на всех input-схемах | `test_unknown_field_rejected_422` |
| S9 | `lazy="raise"` на связях | n/a — 0 `relationship()` | при добавлении связей — только `lazy="raise"` + явные `selectinload/joinedload` | `test_lazy_load_raises` |
| S10 | SQL только параметризованный | ✅ `_escape_like`, bind-параметры | 49 `text()` переносятся в DAO; f-string в SQL запрещён линтером | `ruff`-правило + ревью |
| S11 | Ошибки без утечки внутренностей | ⚠️ часть `HTTPException` с текстом исключения | глобальный handler: `DomainError → 4xx`, `Exception → 500 {code}` без stacktrace | `test_500_has_no_traceback` |
| S12 | Логи без секретов | ✅ прокси маскируются (`_mask_proxy_url`) | правило: `SecretStr` + маскирование в логгере | grep-тест на `password=` в логах |

### 1.3. Аутентификация и лимиты

| # | Требование | Сейчас | Мера | Критерий |
|---|---|---|---|---|
| S13 | Короткий TTL access | ✅ 3600 c | оставить; для админки — 900 c | конфиг-тест |
| S14 | Refresh с ротацией и reuse-detection | ⚠️ TTL 30 дней, ротация не подтверждена | TTL 7–14 дней, одноразовый refresh, при повторе — отзыв всей цепочки | `test_refresh_reuse_revokes_family` |
| S15 | Пароли: argon2id или PBKDF2 с числом итераций | ⚠️ PBKDF2-HMAC (`hashlib.pbkdf2_hmac`), параметры нужно зафиксировать | argon2id (предпочтительно) + rehash-on-login; либо PBKDF2 ≥ 600k итераций, соль ≥ 16 байт | `test_password_hash_params` |
| S16 | Rate limit на HTTP API | ❌ middleware нет; лимиты есть только для Telegram и партнёрского API | Redis sliding window по IP + `user_id`; отдельные лимиты на `/auth/*` (5/мин) и AI (квота уже есть) | `test_rate_limit_429_with_retry_after` |
| S17 | Rate limit на internal API | ❌ | лимит по токену/IP, 429 + `Retry-After` | `test_internal_rate_limited` |
| S18 | Security headers / CORS | ⚠️ Caddy/gateway | HSTS, `X-Content-Type-Options`, CSP для Mini App; CORS — только доверенные origin | `test_security_headers` |
| S19 | Идемпотентность платежей | ✅ `UNIQUE(provider, provider_payment_id)`, `Lead.idempotency_key` | сохранить; для вебхуков — ключ события провайдера | есть тесты |

---

## 2. Производительность

| # | Требование | Сейчас | Мера | Критерий |
|---|---|---|---|---|
| P1 | Фильтр по цене использует индекс | ❌ `case(... price / 1350.0 ...)` — non-sargable | хранимая `price_usd` (пишется при upsert по курсу) + `price_usd_rate`; индекс `(is_active, source, price_usd)`; ночной пересчёт при изменении курса > 1 % | `EXPLAIN` показывает Index Scan; `test_price_filter_uses_index` |
| P2 | Keyset вместо OFFSET | ❌ 6 `.offset()`, лента на `OFFSET` | `WHERE (parsed_at, id) < (:ts, :id) ORDER BY parsed_at DESC, id DESC LIMIT n+1`, курсор в ответе | `test_feed_keyset_stable_under_insert` |
| P3 | Нет `count(*)` на каждый запрос ленты | ❌ отдельный `select(func.count())` | `has_more` + `next_cursor`; total — отдельный кэшируемый эндпоинт, если нужен UX | `test_feed_no_count_query` (query budget) |
| P4 | Индексы под фильтры `lower(col) = ?` | ❌ функциональных индексов нет | `CREATE INDEX ... ON listings (lower(make))`, `(lower(model))`; частичные — `WHERE is_active` | `EXPLAIN` + миграция |
| P5 | Индекс под избранное | ⚠️ PK `(user_id, listing_id)`, сортировка по `created_at` | `(user_id, created_at DESC, listing_id DESC)` | `EXPLAIN` |
| P6 | Индекс под историю цен | ⚠️ `(listing_id, recorded_at)` | `(listing_id, recorded_at, id)` для `DISTINCT ON` | `EXPLAIN` |
| P7 | Eager loading вместо lazy | n/a (0 связей) | `selectinload` для one-to-many, `joinedload` для many-to-one; `lazy="raise"` | query budget тест |
| P8 | Нет запросов в цикле | ⚠️ `save_listings` — bulk upsert (хорошо); `list_favorites` — валидация в цикле (CPU, не SQL) | проверять каждый цикл с `await session...` | `test_list_endpoints_query_budget` |
| P9 | Нет записи на read-path | ❌ `enrich_listing_specs` в `GET /listings/{id}`: сеть + `commit()` | вынести в очередь `enrich_listing`; PDP отдаёт что есть, обогащение — асинхронно | `test_get_listing_does_not_write` |
| P10 | Один `httpx.AsyncClient` на процесс | ❌ `BackendClient` создаёт клиент на вызов (бот); адаптеры — осознанно на запрос из-за прокси | в боте — APP-скоуп клиент с `limits`, keepalive, таймаутами; в адаптерах оставить как есть (документировано) | `test_bot_reuses_http_client` |
| P11 | Пул БД не превышает `max_connections` | ⚠️ 10 + 20 overflow на воркер; при 4 воркерах — до 120 при `max_connections=100` | `pool_size` из расчёта `max_connections / workers`; PgBouncer для API | конфиг-тест + нагрузка |
| P12 | Серверные таймауты | ✅ `statement_timeout=30s`, `lock_timeout=5s`, `idle_in_transaction=60s` | сохранить | конфиг-тест |
| P13 | Кэш фасетов | ✅ Redis 5 мин | сохранить; инвалидация по `listings.updated_at` — опционально | есть тест |
| P14 | Query budget в тестах | ❌ | фикстура-счётчик ниже | каждый коллекционный эндпоинт ≤ N запросов |

### Фикстура query budget (без внешних пакетов)

```python
# tests/conftest.py
import pytest
from sqlalchemy import event


@pytest.fixture
def query_counter(engine):
    """Считает SQL-запросы через событие SQLAlchemy (без pytest-queryguard)."""
    counter = {"n": 0}

    def _before(*args, **kwargs):
        counter["n"] += 1

    event.listen(engine.sync_engine, "before_cursor_execute", _before)
    yield counter
    event.remove(engine.sync_engine, "before_cursor_execute", _before)


@pytest.fixture
def assert_max_queries(query_counter):
    def _assert(limit: int) -> None:
        assert query_counter["n"] <= limit, (
            f"query budget exceeded: {query_counter['n']} > {limit}"
        )

    return _assert
```

```python
@pytest.mark.asyncio
async def test_feed_query_budget(client, assert_max_queries):
    await client.get("/api/listings?limit=20")
    assert_max_queries(3)  # count + page + (опционально) analytics
```

> `pytest-queryguard` из промпта не берём: пакет не проверен, а 20 строк выше
> решают ту же задачу и не добавляют зависимость.

---

## 3. Тесты

### 3.1. Пирамида

| Уровень | Что тестируем | Инфраструктура | Кол-во (цель) |
|---|---|---|---|
| Domain unit | инварианты (`Lead.claim`, `Money`, статусы) | нет | ~120 |
| Application unit | use cases на fake-портах | нет БД | ~80 |
| Infrastructure integration | DAO/репозитории, SQL, индексы | testcontainers Postgres | ~60 |
| Transport contract | HTTP: коды, схемы, IDOR, лимиты | `httpx.ASGITransport` + тестовый контейнер | ~120 |
| Messaging contract | хендлеры, идемпотентность, DLQ | `TestRabbitBroker` + `TestApp` | ~30 |
| Migration | `upgrade head → downgrade -1 → upgrade head` | testcontainers | 2 |

### 3.2. Что сделать с текущими 66 файлами

1. **Вернуть собираемость** (фаза 0): 7 файлов-фрагментов либо удалить,
   либо восстановить импорты и фикстуры.
2. **Дедуп 26 имён** по таблице из [01-AUDIT.md](01-AUDIT.md#6-тесты-дубли-и-структура):
   параметризовать адаптерные тесты, оставить по одному владельцу имени.
3. **Разделить маркеры**: `@pytest.mark.integration` (БД/брокер) и
   `@pytest.mark.unit`; CI гоняет unit на каждый коммит, integration — на PR.
4. **Убрать `FakeSession`-моки из тестов use case**: они проверяют SQL, а не
   поведение. Use case тестируется на fake-репозиториях (dict/list).
5. **Добавить security-набор**: IDOR (404 на чужой ресурс), 401 без токена,
   422 на лишнее поле, 429 на лимит, 403 на бан.

### 3.3. Тестовый контейнер Dishka

```python
# tests/container.py
from dishka import make_async_container

from app.di.providers import SettingsProvider, UseCaseProvider
from tests.fakes import FakeFavoriteRepository, FakeListingRepository, FakeUnitOfWork


class FakeInfraProvider(Provider):
    """Подмена инфраструктуры: use cases тестируются без БД и брокера."""

    scope = Scope.REQUEST

    uow = provide(FakeUnitOfWork, provides=UnitOfWork)
    favorites = provide(FakeFavoriteRepository, provides=FavoriteRepository)
    listings = provide(FakeListingRepository, provides=ListingRepository)


def make_test_container() -> AsyncContainer:
    return make_async_container(
        SettingsProvider(), FakeInfraProvider(), UseCaseProvider()
    )
```

```python
@pytest.mark.asyncio
async def test_toggle_favorite_twice_returns_false():
    container = make_test_container()
    async with container() as request:
        uc = await request.get(ToggleFavorite)
        first = await uc.execute(ToggleFavoriteCommand(user_id=1, listing_id=10))
        second = await uc.execute(ToggleFavoriteCommand(user_id=1, listing_id=10))
    assert first.favorited is True
    assert second.favorited is False  # toggle, не add
```

### 3.4. Контракт очередей

```python
from faststream.rabbit import TestRabbitBroker
from faststream import TestApp


@pytest.mark.asyncio
async def test_fetch_duplicate_is_skipped(container, broker):
    async with TestRabbitBroker(broker), TestApp(broker):
        await broker.publish(cmd, queue=FETCH_QUEUE)
        await broker.publish(cmd, queue=FETCH_QUEUE)  # тот же idempotency_key
    assert fake_fetch.calls == 1
```

### 3.5. CI-гейты

```yaml
# минимальный набор
- ruff check app tests
- mypy app
- pytest -m "unit" -q
- pytest -m "integration" -q          # PR only
- alembic upgrade head && alembic downgrade -1 && alembic upgrade head
- pip-audit
- bandit -r app -ll
```

Критерий готовности чек-листа: любой пункт ❌ закрыт тестом, который
падает на старом коде и проходит на новом. Без такого теста пункт не
считается закрытым.
