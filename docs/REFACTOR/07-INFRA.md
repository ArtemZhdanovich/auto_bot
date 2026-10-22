# 07. Тестовая инфраструктура и подготовка БД

**Дата:** 21.10.2026
**Ветка:** `develop` (HEAD `f83de19`, рабочее дерево уже было грязным — см. §7)
**Область:** только `postgres`, `redis`, `rabbitmq`. Backend, bot, worker_*, qdrant, minio, rag, translate — **не запускались**.

---

## 1. Сверка образов compose ↔ локальный кэш

`docker-compose.yml` объявляет только три инфраструктурных сервиса с фиксированными тегами:

| Сервис | image в compose | Найден локально (`docker image ls`) | Ports | Healthcheck |
|---|---|---|---|---|
| postgres | `postgres:16` | да, `ca0bd484cb98`, 636 MB | `5432:5432` | `pg_isready -U app` |
| redis | `redis:7-alpine` | да, `858f009f9709`, 57.3 MB | `6379:6379` | `redis-cli ping` |
| rabbitmq | `rabbitmq:3-management` | да, `e582c0bc7766`, 389 MB | `5672:5672`, `15672:15672` | `rabbitmq-diagnostics -q ping` |

Теги совпали, скачивание не потребовалось: запуск шёл с `--pull never --no-build --no-deps`.
Env postgres: `POSTGRES_DB=autoselect`, `POSTGRES_USER=app`, `POSTGRES_PASSWORD=${DB_PASSWORD:-app}`.
В `.env` `DB_PASSWORD` совпадает с паролем внутри `DATABASE_URL` (проверено сравнением строк, в документацию пароль не выносится).

---

## 2. Статус контейнеров

Ранее контейнеры существовали в состоянии `Exited (255)` — данные в named-volume `pg_data` сохранились, контейнеры были запущены заново, **не пересоздавались**, `down -v` не выполнялся.

```
NAME                  IMAGE                   STATE     HEALTH    PORTS
auto_bot-postgres-1   postgres:16             running   healthy   0.0.0.0:5432->5432/tcp
auto_bot-redis-1      redis:7-alpine          running   healthy   0.0.0.0:6379->6379/tcp
auto_bot-rabbitmq-1   rabbitmq:3-management   running   healthy   0.0.0.0:5672->5672/tcp, 0.0.0.0:15672->15672/tcp
```

Все три стали `healthy` на первом же опросе после `up -d`; лимит ожидания 120 с не понадобился.

Версии из живых сервисов:

| Сервис | Версия | Как получено |
|---|---|---|
| PostgreSQL | `16.15 (Debian 16.15-1.pgdg13+2)` | `show server_version` через asyncpg |
| Redis | `7.4.11` | `INFO server` через redis-py |
| RabbitMQ | `3.13.7` | `GET http://localhost:15672/api/overview` → HTTP 200 |

---

## 3. Подключения и DSN

DSN из `.env` (в файл вынесен с маскированным паролем):

```
postgresql+asyncpg://app:<DB_PASSWORD>@localhost:5432/autoselect
redis://localhost:6379/0
amqp://guest:guest@localhost:5672/
```

| Проверка | Результат |
|---|---|
| Postgres через `.venv\Scripts\python.exe` + `asyncpg` | подключение успешно; `current_database()=autoselect`, `current_user=app` |
| `psycopg` в venv | отсутствует (`ModuleNotFoundError`) — проекту не нужен, используется asyncpg |
| Redis `ping()` | `True` |
| RabbitMQ TCP 5672 / 15672 | `True` / `True` |
| RabbitMQ management API | HTTP `200`, `rabbitmq_version=3.13.7` |

---

## 4. Миграции

Прогон из корня проекта виртуальным окружением проекта:

```
.venv\Scripts\python.exe -m alembic current   # 0018 (head)
.venv\Scripts\python.exe -m alembic heads     # 0018 (head)
.venv\Scripts\python.exe -m alembic upgrade head
```

`upgrade head` завершился с кодом `0` и без ошибок: БД уже была на головной ревизии `0018`
(`app/db/migrations/versions/0018_inspection_reports.py`), применить было нечего. Миграции не изменялись.

`alembic.ini` → `script_location = app/db/migrations`; URL берётся не из ini, а из `get_settings().database_url` (`app/db/migrations/env.py`), то есть из `.env`.

---

## 5. Схема основной БД `autoselect`

19 таблиц в `public` (18 доменных + `alembic_version`):

```
admin_actions, alembic_version, analytics_events, dialog_sessions, favorites, filters,
inspection_reports, leads, listing_price_history, listings, partner_accounts,
partner_api_keys, partner_transactions, payments, price_analyses, sent_log,
source_health, sources, users
```

Обязательный минимум из брифа присутствует: `users`, `listings`, `favorites`, `leads` — все `True`.
`alembic_version.version_num = 0018`.

Контроль сохранности данных (только чтение `count(*)`, до и после работ записи в основную БД не было):
`users=20`, `listings=1929`, `favorites=0`, `leads=0`, `sources=3`, `filters=2`.

---

## 6. Тестовая БД

`autoselect_test` **уже существовала** — `CREATE DATABASE` не выполнялся, основная БД не затрагивалась.

| Параметр | Значение |
|---|---|
| Наличие | есть (`pg_database`) |
| Размер | 9527 kB (основная — 11 MB) |
| Таблиц в `public` | 18, без `alembic_version` |
| Права | `has_database_privilege(app, autoselect_test, 'CREATE') = True` |

Схему тестовой БД создаёт сам pytest, а не alembic: `tests/conftest.py::engine` (session-scope)
делает `Base.metadata.drop_all` + `create_all` по DSN `TEST_DATABASE_URL` либо `<database>_test`,
и при недоступности БД скипает зависимые тесты. Поэтому отсутствие `alembic_version` в тестовой
БД — ожидаемое состояние, а не дефект.

Смоук DB-слоя на `autoselect_test` (`drop_all` → `create_all` → `insert`/`select`/`delete` в `users`):
`CREATED_TABLES: 18`, `INSERT_SELECT_OK: True`, `SMOKE_OK`.

---

## 7. Наблюдения вне области задачи

1. **Рабочее дерево грязное до начала работ.** `git status` показывает большое число уже
   изменённых файлов (`app/adapters/*`, `app/api/*`, `app/ai/*` и др.). Эти изменения не мои:
   в рамках задачи не правился ни один файл кода, не делалось коммитов. Единственный новый
   файл — этот документ.
2. **`tests/test_leads.py` красный по причине уровня кода, не инфраструктуры.** Прогон
   `-m pytest tests\test_leads.py -q` дал `4 failed, 5 passed, 6 errors`; в логе
   `POST http://test/api/leads → 404`, хотя роутер `app/api/leads.py` объявлен с
   `prefix="/api/leads"` и подключён в `app/main.py:140`. Причина не в БД: схема в тестовой
   БД поднялась, смоук записи/чтения прошёл.
3. **`tests/test_dedup.py` + `tests/test_dialog_store.py`**: `8 failed, 14 passed`; падения —
   `NameError`/`AttributeError` на несуществующих именах (`_dedup_*`, `clai*`), то есть
   незавершённое состояние рефакторинга. Не исправлялось (вне области задачи).
4. **Не запускались** `qdrant`, `minio` и `rag_*`. Если интеграционным тестам нужны RAG/векторный
   поиск или S3-хранилище, их придётся поднимать отдельно — в текущем окружении они `Exited`.

---

## 8. Точные сработавшие команды

PowerShell: разделитель `&&` не поддерживается, использовался `;`. `docker compose` пишет прогресс
в stderr, из-за чего PowerShell показывает `NativeCommandError` — код возврата при этом `0`.

```powershell
cd C:\Users\Pashtet\Desktop\projects\auto_bot
git branch --show-current                                  # develop
docker image ls --format "{{.Repository}}:{{.Tag}} {{.ID}} {{.Size}}"
docker compose ps -a --format "table {{.Name}}\t{{.Service}}\t{{.Status}}\t{{.Ports}}"

docker compose up -d --no-deps --no-build --pull never postgres redis rabbitmq

# ожидание healthy (цикл до 120 с)
docker compose ps postgres redis rabbitmq --format "{{.Service}}|{{.State}}|{{.Health}}"

# Postgres / Redis / RabbitMQ
.venv\Scripts\python.exe <temp>\autobot_infra_check.py       # asyncpg: server_version, current_database
.venv\Scripts\python.exe -c "import redis; print(redis.Redis(host='localhost',port=6379,db=0).ping())"
Test-NetConnection -ComputerName localhost -Port 5672
Test-NetConnection -ComputerName localhost -Port 15672
# management API: GET http://localhost:15672/api/overview (guest:guest) → 200

# миграции
.venv\Scripts\python.exe -m alembic current
.venv\Scripts\python.exe -m alembic heads
.venv\Scripts\python.exe -m alembic upgrade head             # exit 0

# таблицы/версия/список БД и смоук тестовой БД — тем же интерпретатором (скрипты в %TEMP%)

# точечные прогоны тестов
.venv\Scripts\python.exe -m pytest tests\test_leads.py -q --no-header -p no:cacheprovider
.venv\Scripts\python.exe -m pytest tests\test_dedup.py tests\test_dialog_store.py -q --no-header -p no:cacheprovider
```

---

## 9. Что не проверено

- Полный прогон `pytest` (62 файла тестов) не запускался — только два точечных файла для проверки
  доступности БД. Итоговый baseline нужно снимать отдельно.
- Поведение приложения (backend/bot/worker) на этой инфраструктуре не проверялось: по условию
  задачи эти сервисы не поднимались.
- Устойчивость к перезапуску хоста/докера и поведение при занятых портах не проверялись.
- Management UI RabbitMQ проверен только HTTP-запросом к `/api/overview`, не через браузер.
