# 06. Триаж красных тестов

> **Статус на 21.10.2026, вечер:** все категории закрыты, suite зелёный
> (1076 passed, 3 skipped). Документ сохранён как история triage: он
> показывает, из чего состояли 270 падений и какие из них были дефектами
> продукта. Актуальный статус — [05-STATUS.md](05-STATUS.md).

**Дата:** 21.10.2026
**Ветка:** `develop`, HEAD `f83de19` (рабочее дерево грязное — незакоммиченные правки `app/` и `tests/`)
**Задача:** классифицировать причины красных тестов, не чиня их. Ничего в `app/` и `tests/` не менялось.

---

## 0. Главное

1. Заявленный baseline **воспроизведён 1:1**: `710 passed, 165 failed, 181 errors` за 97.68 с (17:34:57).
2. **Замер устарел через минуту после прогона.** `tests/conftest.py` переписан в **17:36:10** (в HEAD — 179 строк и нет `client`; сейчас 407 строк): добавлены `engine`, `session`, `client`, `http`, `internal_token`, `user`, `auth_headers`, `auth_headers_admin`, `inactive_user`, `query_counter`. Повторный прогон того же дерева: **790 passed, 224 failed, 46 errors** (1060 тестов).
3. Поэтому «181 error = нет фикстур `client`/`session`» из [05-STATUS.md](05-STATUS.md) §3.1 **больше не верно**. Остались 46 errors, но из-за **другого, гораздо более узкого** набора отсутствующих фикстур (`free_user`, `pro_user`, `listing`, `lead`, `fake_session_factory`, `session_empty`, `guest_headers`, `session_with_analysis`, `auth_headers_inactive_user`).
4. **Категория D пуста.** Postgres (5432), Redis (6379), RabbitMQ (5672) доступны, контейнер `auto_bot-postgres-1` — `healthy`. Ни один тест не красный из-за отсутствия инфраструктуры. Более того, `engine`/`session` в новом conftest **уже работают с реальной БД** (`_test_database_url()` → `autoselect_test`, схема пересоздаётся на сессию).
5. Актуальная картина: **270 красных из 1060** — B 127, A 79, C 47, E 17. Реальный дефект продукта ровно один и он серьёзный: **`app/core/pq_crypto.py` неработоспособен на установленной `cryptography 50.0.2`** (7 errors).

---

## 1. Как мерили

| Снимок | Команда | Когда | Всего | passed | failed | errors |
|---|---|---|---|---|---|---|
| **R1** (запрошенный) | `pytest -q --tb=line --junitxml=tmp_triage.xml` | 17:33:19 → 17:34:57 | 1056 | 710 | 165 | 181 |
| **R2** (актуальный) | `pytest -q --tb=line --junitxml=tmp_triage2.xml` | 17:39 → 17:41 | 1060 | 790 | 224 | 46 |

Причина расхождения — не регрессия, а **параллельная правка дерева**:

- `tests/conftest.py`: `LastWriteTime 17:36:10`, 14088 байт, 407 строк; в R1 pytest печатал `available fixtures: ... fake_redis, fake_session, ...` — то есть новых фикстур в момент R1 не существовало.
- Появился новый файл `tests/test_trace.py` (+4 теста: 1056 → 1060).
- Первый повторный прогон я прервал: он шёл параллельно с точечными прогонами по той же тестовой БД (conftest делает `drop_all`/`create_all` на сессию), результаты были бы недостоверны. R2 снят монопольно.

**Вывод по методике:** цифры ниже — из R2. Колонки R1 оставлены, чтобы видеть, что именно «открылось» после появления фикстур: 135 errors превратились в 59 новых failures + 80 passed.

---

## 2. Таблица по файлам

Столбцы: `R1` = всего/упало/ошибок до правки conftest, `R2` = после. Категория — по R2 (доминирующая причина; вторичные перечислены в тексте причины).

| Кат. | Файл | R1 (всего/f/e) | R2 (всего/f/e) | Причина в R2 (дословно из traceback) |
|---|---|---|---|---|
| A | `tests/test_notifications_api.py` | 24/3/20 | 24/21/2 | `AttributeError: 'tuple' object has no attribute 'post'` ×20 (локальная фикстура `client` возвращает `(client, session)`); `fixture 'free_user'/'pro_user' not found` |
| A | `tests/test_dialog_api.py` | 18/2/13 | 18/10/1 | `404 Not Found` ×5 (нет пользователя в БД); `NameError: _enqueue_fetch`, `_ok_agent`; `fixture 'session_empty' not found` |
| A | `tests/test_ai_search.py` | 11/1/8 | 11/9/0 | `'tuple' object has no attribute 'post'` ×8; `RuntimeError: no enabled sources` |
| A | `tests/test_analysis_api.py` | 19/4/11 | 19/8/3 | `SimpleNamespace has no attribute 'llm_provider'` ×4; `ImportError: No module named 'app.api.price_analysis'` ×4; `fixture 'guest_headers'/'guest_user'/'session_with_analysis' not found` |
| A | `tests/test_leads.py` | 15/3/9 | 15/4/6 | `fixture 'listing' not found` ×3, `fixture 'lead' not found` ×3; `404` ×2; `KeyError: 'id'` |
| A | `tests/test_registry.py` | 23/2/11 | 23/2/11 | `fixture 'fake_session_factory' not found` ×11; `FakeResult has no attribute 'scalar_one_or_none'`; `KeyError: 'Adapter encar not loaded'` |
| A | `tests/test_me.py` | 10/0/9 | 10/1/1 | `fixture 'auth_headers_inactive_user' not found`; гонка `onboarded` (см. E) |
| B | `tests/test_listings.py` | 53/0/53 | 53/11/0 | `422` на `PUT /inspection` ×6 (grade `"B"` при `GRADES = ("excellent","good","fair","poor")`); `FakeRedis has no attribute 'exists'` ×2; `assert 'Toyota' in ['BMW','Kia']`; `KeyError: 'id'` |
| B | `tests/test_encar.py` | 33/12/0 | 33/12/0 | `NameError: _criteria` ×6, `_dummy_criteria` ×4; `Failed: DID NOT RAISE ValueError`; `assert False is True` (healthcheck) |
| B | `tests/test_kcar.py` | 30/11/0 | 30/11/0 | те же `_criteria` ×5 / `_dummy_criteria` ×4; `DID NOT RAISE ValueError`; healthcheck |
| B | `tests/test_che168.py` | 20/10/0 | 20/10/0 | `NameError: HttpClient` ×5, `FetchError` ×2, `HEADERS` ×1; `_safe_join('https://www.che168.com/','//evil.com/a/1')` → `'https://evil.com/a/1'`; `parse_wan_number('23.80万')` |
| B | `tests/test_dedup.py` | 12/8/0 | 12/8/0 | `NameError: claim` ×2, `_dedup_keys`, `Decimal`, `asyncio`; `FakeRedis has no attribute 'eval'` ×2; ключ `dedup:v2:vin:…` vs ожидаемый `dedup:vin:…` |
| B | `tests/test_llm_client.py` | 24/10/0 | 24/10/0 | `NameError: asyncio` ×8; `NameError: _settings` ×2 |
| B | `tests/test_security_initdata.py` | 27/10/0 | 27/10/0 | `NameError: json` ×7; `InitDataError: initData: expired` ×2; `Actual message: 'Invalid token'` |
| B | `tests/test_payments_api.py` | 10/6/0 | 10/6/0 | `'str' object has no attribute 'get_secret_value'` ×3 (тест кладёт `str` в `settings.bot_token`); `SimpleNamespace has no attribute 'is_active'` ×2; `17000.0 != 18000.0` |
| B | `tests/test_miniapp_api.py` | 26/6/0 | 26/6/0 | `SimpleNamespace has no attribute 'is_active'` ×6 |
| B | `tests/test_proxy.py` | 19/5/0 | 19/5/0 | `NameError: asyncio` ×2, `SecretStr` ×2, `fake_redis` ×1 |
| B | `tests/test_monitoring.py` | 11/4/0 | 11/4/0 | `'str' object has no attribute 'get_secret_value'` ×2; `RAGClient._record_failure() got an unexpected keyword argument 'fail_threshold'` |
| B | `tests/test_tools.py` | 35/4/0 | 35/4/0 | `apply_patch` сохраняет неизвестное поле `year_from` ×2; `'tuple' object has no attribute 'append'` (тест ждёт `TypeError`) |
| B | `tests/test_internal_token_empty_rejected.py` | 18/5/5 | 18/10/0 | `'coroutine' object has no attribute 'status_code'` ×5 (забыт `await`); `DID NOT RAISE ValidationError` ×2; `NameError: HTTPException/get_redis/internal_token` |
| B | `tests/test_metrics.py` | 10/0/10 | 10/2/1 | `FakeRedis has no attribute 'exists'`; `NotNullViolationError: null value in column "subscription_tier"` (тест вставляет NULL в NOT NULL); `fixture 'listing' not found` |
| B | `tests/test_admin_api.py` | 26/1/11 | 26/2/0 | `404 Not Found`; diff без `-v` |
| B | `tests/test_auth_api.py` | 9/1/0 | 9/1/0 | ссылка `https://t.me/@my_auto_bot?start=login_…` (см. E) |
| B | `tests/test_onboarding.py` | 3/2/0 | 3/2/0 | `FakeSession.committed is True`, `assert None is not None` — локальный стаб не воспроизводит поведение |
| B | `tests/test_prod_secrets.py` | 4/2/0 | 4/2/0 | `'SecretStr' object has no attribute 'startswith'`; pydantic `value_error` |
| B | `tests/test_che168_adapter.py` | 13/1/0 | 13/1/0 | `Decimal('238000')` vs `Decimal('238000')` — расхождение единиц |
| B | `tests/test_services_smoke.py` | 20/2/0 | 20/2/0 | `assert '100' == 100.0`; `'str' object has no attribute 'get_secret_value'` |
| B | `tests/test_http_client.py` | 11/1/0 | 11/1/0 | `httpx.RemoteProtocolError: boom` — контракт ретраев |
| B | `tests/test_calc_api.py` | 18/1/0 | 18/1/0 | `422` на compare со «плохими» элементами |
| B | `tests/test_normalize.py` | 64/1/0 | 64/1/0 | `Мерседес Бенц` не нормализуется |
| B | `tests/test_fetch.py` | 24/1/0 | 24/1/0 | `FakeRedis has no attribute 'eval'` |
| B | `tests/test_admin_assistant.py` | 7/1/0 | 7/1/0 | `RuntimeError: No active exception to reraise` (неверное использование `pytest.raises`) |
| B | `tests/test_agent.py` | 18/2/0 | 18/2/0 | `assert 'collecting' == 'searching'` ×2 — состояние FSM переименовано |
| C | `tests/test_partners.py` | 37/28/0 | 37/28/0 | `module 'app.services.partners' has no attribute 'hash_key'` ×17, `generate_api_key`, `authenticate_key`; `_Result has no attribute 'one_or_none'` ×4; `SeqSession.execute() takes 2 positional arguments but 3 were given` ×2 |
| C | `tests/test_partner_billing.py` | 14/0/14 | 14/0/14 | setup: `AttributeError: <module 'app.services.partners'> has no attribute …` — `mock.patch` на удалённую функцию |
| C | `tests/test_storage.py` | 14/3/0 | 14/3/0 | `admin_listing_raw() takes from 1 to 2 positional arguments but 3 were given` ×2; `'raw' in {...}` |
| C | `tests/test_funnels.py` | 7/2/0 | 7/2/0 | `analytics_funnel() got multiple values for argument 'days'` — тест передаёт `internal_token()` вторым позиционным |
| E | `tests/test_pq_crypto.py` | 7/0/7 | 7/0/7 | setup: `PQCryptoError: MLKEM768PrivateKey.private_bytes_raw() returned 64 bytes, expected 2400` |
| E | `tests/test_kcar_adapter.py` | 11/4/0 | 11/4/0 | `FetchResult(items=[], … source_meta={'total': 8857, 'fetched': 0, 'truncated': True})` ×2; `FetchError: kcar: manufacturer 'Zaz' is not supported`; `IndexError: list index out of range` |
| E | `tests/test_encar_adapter.py` | 12/3/0 | 12/3/0 | `FetchError: encar: manufacturer 'Lada' is not supported by the API`; `assert False is True` и `assert True is False` (healthcheck) |
| E | `tests/test_rag.py` | 25/3/0 | 25/3/0 | 15 HTTP-запросов вместо 5 до открытия breaker; `cached=True, latency_ms=0`; `RAGUnavailable: rag_bad_response` |

**Зелёные файлы (22, 268 тестов):** `test_admin_audit`, `test_analytics`, `test_auth_guest`, `test_bot_analysis`, `test_bot_calc`, `test_bot_dialog_ui`, `test_bot_inline_share`, `test_bot_throttle`, `test_cron`, `test_delivery`, `test_dialog_store`, `test_gateway_proxy`, `test_gateway_routing`, `test_heartbeat`, `test_notify`, `test_parser_doctor`, `test_price_analysis`, `test_rate_limit`, `test_scheduler`, `test_send_worker`, `test_trace`, `test_translate`.

---

## 3. Сводка по категориям

| Кат. | Что это | Файлов | Тестов в файлах | failed | errors | Красных | Доля красных |
|---|---|---|---|---|---|---|---|
| **A** | Нет тестового harness: отсутствуют фикстуры или локальная фикстура отдаёт не то, что ждёт тест | 7 | 120 | 55 | 24 | **79** | 29 % |
| **B** | Артефакт: нет helper-функций/импортов, неполные тест-даблы, устаревшие ожидания | 26 | 545 | 126 | 1 | **127** | 47 % |
| **C** | Тест ждёт удалённый/переименованный API | 4 | 72 | 33 | 14 | **47** | 17 % |
| **D** | Нужна инфраструктура (Postgres/Redis/RabbitMQ) | **0** | — | — | — | **0** | 0 % |
| **E** | Похоже на реальный баг продукта | 4 | 55 | 10 | 7 | **17** | 6 % |
| | **Итого** | **41** | **792** | **224** | **46** | **270** | 100 % |

Проверка сходимости: `270 красных + 522 passed в этих 41 файле + 268 passed в 22 зелёных = 1060`; `failed 224`, `errors 46`.

### Почему D пуста

`Test-NetConnection 127.0.0.1:5432/6379/5672` → `True/True/True`; `docker ps` → `auto_bot-postgres-1 … Up (healthy) 0.0.0.0:5432->5432`. Новый conftest уже поднимает `engine` на `autoselect_test` и скипает зависимые тесты только если БД реально недоступна. Категория D была верна на момент [05-STATUS.md](05-STATUS.md) §3.3, **сейчас — нет**.

### Что важно понять про A

79 красных в A — это **не «нет фикстур вообще»**, а три разных дефекта harness:

1. `fixture 'X' not found` — 24 errors в 7 файлах: `free_user`, `pro_user`, `listing`, `lead`, `fake_session_factory`, `session_empty`, `guest_headers`, `guest_user`, `session_with_analysis`, `auth_headers_inactive_user`. Новый conftest дал базовый набор (`user`, `auth_headers`, `inactive_user`), но не эти.
2. `'tuple' object has no attribute 'post'` — 28 failures: локальные фикстуры `client` в `test_notifications_api.py:15`, `test_ai_search.py:24`, `test_dialog_api.py:16` возвращают `(client, session)`, а тело теста вызывает `client.post(...)`. Пока фикстуры не было, эти тесты падали на setup и дефект был не виден.
3. `404 Not Found` — 7 failures: тесты рассчитывают на стаб (`FakeSession.get` находил объект среди `add()`), а на реальной БД пользователя/листинга нет. Это следствие перехода на настоящую сессию, а не баг API.

---

## 4. Категория E: 10 подозрительных случаев

Отсортировано по вероятности реального дефекта. «Гипотеза» — что именно проверить, а не готовый диагноз.

| # | Файл:строка | Тест | Симптом | Гипотеза | Уверенность |
|---|---|---|---|---|---|
| 1 | `tests/test_pq_crypto.py:13` | `test_keypair_sizes` (+6) | setup падает: `private_bytes_raw() returned 64 bytes, expected 2400` | `app/core/pq_crypto.py:85` требует полный FIPS 203 `dk` (2400 Б), а `cryptography 50.0.2` возвращает 64-байтовый seed. `generate_keypair()` не работает → шифрование документов at rest неработоспособно **в проде**, не только в тестах | **Высокая** |
| 2 | `tests/test_kcar_adapter.py:79` | `test_fetch_maps_to_unified_listing` | `items=[]` при `source_meta={'total': 8857, 'fetched': 0}` | `app/adapters/kcar.py:287` читает `data.rows`, `totalCnt` при этом распознан (8857), значит строки есть, но `_map_item` вернул `None` для всех. Похоже на переименование обязательного поля (`carCd`/`prc`/`prdcnYr`) — источник молча отдаёт «0 объявлений» вместо ошибки | Высокая |
| 3 | `tests/test_kcar_adapter.py:168` | `test_price_unit_from_config` | `IndexError: list index out of range` | Маппинг цены предполагает непустой результат; при пустом `rows` падает вместо понятной ошибки. Тот же корень, что №2 | Средняя |
| 4 | `tests/test_encar_adapter.py:154` | `test_build_q_skips_unknown_make_and_model` | `FetchError: manufacturer 'Lada' is not supported by the API` | В диффе WIP `-if kr_make:` заменено на `+if kr_make is None: raise FetchError` (`encar.py:384`, `kcar.py:151`). Раньше неизвестная марка просто опускала фильтр, теперь **любой** неподдерживаемый фильтр обрывает весь fetch. Нужно решение: 400 пользователю или partial fetch | Высокая (по диффу), вопрос к UX |
| 5 | `tests/test_encar_adapter.py:169` и `:176` | `test_healthcheck_true_on_count_only`, `…_false_on_empty_payload` | `assert False is True`, `assert True is False` — вердикты инвертированы | `encar.py:294` читает `Count`/`SearchResults` из корня payload; похоже, конверт ответа сместился (или фикстура даёт другой уровень). Ошибка healthcheck → источник либо ложно отключается, либо не отключается при сломанном контракте | Средняя |
| 6 | `tests/test_rag.py:117` | `test_circuit_breaker_opens_after_threshold` | 15 HTTP-запросов на 5 логических сбоев | `_DEFAULT_FAIL_THRESHOLD = 5` (`rag_client.py:41`), но `max_retries=2` ретраит и 5xx, которых тест считает неретраибельными. Breaker открывается позже, каждый сбой тянет backoff → задержки в диалоге | Средняя |
| 7 | `tests/test_normalize.py:100` | `test_mercedes_variants[Мерседес Бенц]` | `Мерседес Бенц` не приводится к `Mercedes-Benz` | Пробел в словаре брендов → фильтр по марке не находит объявления с кириллическим написанием | Средняя |
| 8 | `tests/test_services_smoke.py:180` | `test_smoke_partners_me` | `assert '100' == 100.0` | Числовое поле отдаётся строкой (вероятно, `Decimal` без сериализации) → клиенты получают строку вместо числа | Средняя |
| 9 | `tests/test_payments_api.py:268` | `test_list_favorites` | `17000.0 != 18000.0` | Расхождение цены в избранном: возможно, тест ждёт старую семантику `price` (KRW), а API перешёл на `price_usd`. Денежное поле — проверить приоритетно | Низкая-средняя |
| 10 | `tests/test_auth_api.py:40` | `test_link_built_from_bot_username` | URL содержит `@`: `https://t.me/@my_auto_bot` | `config.py:98` снимает `@` только в валидаторе `Settings`, а `auth.py:402` подставляет значение как есть. В проде env проходит валидатор, поэтому это латентная хрупкость, а не регрессия | Низкая |

**Не вошло в E, но стоит помнить:** `tests/test_me.py:75` (два параллельных `POST /onboarded` — один не 200) — скорее артефакт теста (обе ветки используют одну `AsyncSession`), но атомарности UPSERT там действительно нет. `tests/test_tools.py:197` (`apply_patch` сохраняет неизвестное поле) — расхождение контракта валидации, неясно, какая сторона верна.

---

## 5. Ловушки: это **не** баг продукта

Важно, чтобы фазу 1 не начали с «починки» работающего кода.

| Симптом | Почему не баг | Где смотреть |
|---|---|---|
| `partners has no attribute 'hash_key' / 'generate_api_key' / 'authenticate_key'` (19 тестов) | В `app/services/partners.py` теперь `_new_raw_key()`; **в `app/` нет ни одного вызова** удалённых имён — они не используются продуктом, тесты устарели | `app/services/partners.py:64` |
| `partner_billing` setup: `mock.patch` на отсутствующий атрибут | То же: патч на удалённую функцию | `tests/test_partner_billing.py` |
| `analytics_funnel() got multiple values for argument 'days'` | Тест зовёт `analytics_funnel(session, internal_token(), days=14)`; актуальная сигнатура — `(session, days)`, токен проверяется на уровне роутера `dependencies=[Depends(check_internal_token)]` | `app/api/admin.py:408`, `:45` |
| `admin_listing_raw() takes from 1 to 2 positional arguments but 3 were given` | Тот же паттерн: тест передаёт токен третьим аргументом, эндпоинт принимает `(session, listing_id)` | `app/api/admin.py:377` |
| `'SecretStr' object has no attribute 'startswith'`, `'str' object has no attribute 'get_secret_value'` | Продукт хранит секреты в `SecretStr` и разворачивает их через `.get_secret_value()`; тесты подменяют поля на `str` или вызывают строковые методы у `SecretStr` | `app/core/config.py:51`, `app/api/payments.py:130` |
| `'tuple' object has no attribute 'append'` в `test_tools_is_immutable` | `TOOLS` стал tuple, тест ждёт `TypeError` от `list.append`, а получает `AttributeError`. Продукт стал **строже**, тест просто устарел | `tests/test_tools.py:292` |
| `422` на `PUT /inspection` (6 тестов) | Шкала оценок изменена на `excellent/good/fair/poor` (`GRADES`), тест присылает `"B"`. Тест надо обновить, но это же и breaking change API | `app/schemas/listing.py:146` |
| `ImportError: No module named 'app.api.price_analysis'` | Модуля **никогда не было в git** (`git log --all -- app/api/price_analysis.py` пуст); эндпоинт живёт в `app/api/analysis.py`. Тесты — из старой раскладки | `tests/test_analysis_api.py:241` |

---

## 6. Приоритет фазы 1

Порядок — по «красных на час работы» и по риску. Оценки грубые, без учёта ревью.

### Шаг 1. B — механические артефакты (127 красных, 26 файлов). ~4–6 ч
Три подпакета, каждый проверяется отдельным прогоном:
1. **Импорты и helper-функции** (~78): `_criteria`/`_dummy_criteria` (encar, kcar), `HttpClient`/`FetchError`/`HEADERS` (che168), `asyncio` (llm_client, proxy), `json` (security_initdata), `claim`/`_dedup_keys`/`Decimal` (dedup), `_settings`, `_enqueue_fetch`, `_ok_agent`. Механика, риск нулевой.
2. **Тест-даблы** (~25): `FakeRedis.eval` (Lua из `dedup.py`), `FakeRedis.exists`, `_Result.one_or_none`, `FakeResult.scalar_one_or_none`, поля `is_active`/`llm_provider` в `SimpleNamespace`. Обновлять **даблы**, а не продукт.
3. **Устаревшие ожидания** (~24): шкала `GRADES`, `dedup:v2:` префикс, состояние `collecting` vs `searching`, `TOOLS` как tuple.

### Шаг 2. A — добить harness (79 красных, 7 файлов). ~3–4 ч
1. Добавить в `tests/conftest.py`: `free_user`, `pro_user`, `listing`, `lead`, `guest_headers`, `guest_user`, `session_empty`, `session_with_analysis`, `auth_headers_inactive_user`, `fake_session_factory`. Большинство — 5–10 строк поверх уже готовых `session`/`user`.
2. Починить локальные `client`-фикстуры, возвращающие tuple (3 файла, 28 тестов) — самый дешёвый участок.
3. Для 404-тестов — создавать данные через фикстуры, а не полагаться на стаб.

### Шаг 3. C — переписать под текущий API (47 красных, 4 файла). ~3 ч
`test_partners.py` (28) и `test_partner_billing.py` (14) — переписать на `_new_raw_key`/`hash_api_key`, либо удалить, если покрытие ушло в контрактные тесты API. `test_storage.py` (3) и `test_funnels.py` (2) — убрать лишний аргумент токена.

### Шаг 4. E — разбор, не починка (17 красных, 4 файла). ~1 день на решения
1. **`pq_crypto` — P0.** Это единственный подтверждённый дефект продукта: фича не работает на текущей версии библиотеки. Решение: либо принимать 64-байтовый seed (тогда меняется формат контейнера и `_split_private`), либо пиновать `cryptography`. **Не** «чинить» тест.
2. **`kcar_adapter` — P0.** Источник отдаёт 0 объявлений при непустом `total` — тихая деградация парсера.
3. `encar`/`kcar` `FetchError` на неизвестную марку — принять продуктовое решение (400 vs partial).
4. `healthcheck`, `rag` breaker, `normalize`, `services_smoke` — P1, после зелёного B/A.

### Гейт фазы 1
`pytest -q` → **0 errors**, красные только в E-файлах и только с явной пометкой `@pytest.mark.product_issue`. После этого — маркеры `integration` для тестов, требующих БД, и только затем фаза 2 (домен).

---

## 7. Ограничения этого замера

1. **Дерево менялось во время работы.** `tests/conftest.py` переписан в 17:36:10, появился `tests/test_trace.py`. R1 и R2 — разные состояния дерева; R2 актуален на 17:41.
2. **R1 больше не воспроизводится** на текущем дереве: рабочая версия conftest до 17:36 утрачена (в HEAD лежит другая — 179 строк, без `client`). Файл `tmp_triage.xml` (R1) сохранён как артефакт; `tmp_triage2.xml` удалён после разбора, R2 воспроизводится командой из §1.
3. **Одна тестовая БД на всех.** Параллельные прогоны недопустимы (conftest делает `drop_all`/`create_all`). Первый повторный прогон был прерван именно поэтому.
4. **Категория присвоена файлу, а не тесту.** В 6 файлах есть по 1–2 теста другой категории (помечено в тексте причины). Точная разбивка по тестам — в `tmp_triage.xml` (атрибуты `classname`/`name`).
5. **E — это гипотезы, а не приговор.** Проверены по коду только №1–№4; остальные требуют отдельного разбора с решением продукта.
