# RUNBOOK — инциденты и регулярные операции

Каждый рецепт: симптомы → диагностика → лечение → проверка. Журнал
аудитов и постмортемы — [INCIDENTS.md](../INCIDENTS.md); инфраструктурная
справка — [OPERATIONS.md](OPERATIONS.md).

Общие первичные команды:

```bash
docker compose ps                                  # кто unhealthy/restarting
docker compose logs --since 30m <svc> | tail -100  # последние логи
curl -s http://localhost:8000/health/detailed      # состояние ядра
```

---

## И-1. Сервис не стартует (restart loop / unhealthy)

**Симптомы.** `docker compose ps` показывает `Restarting`/`unhealthy`;
`start.bat` падает на шаге ожидания API.

**Диагностика.**

```bash
docker compose logs <svc> | head -50          # traceback на старте
docker compose ps postgres redis              # ядро зависит от них (service_healthy)
```

Типовые причины по логу:

| Лог | Причина | Лечение |
|---|---|---|
| `ValueError: APP_SECRET_KEY …` / `INTERNAL_TOKEN обязателен в prod` | fail-fast `_prod_secrets_required` ([app/config.py](../app/config.py)) | задать секреты в `.env` (≥32 симв., отдельный `INTERNAL_TOKEN`), `docker compose up -d <svc>` |
| `connection refused …:5432` / `:6379` | postgres/redis ещё не healthy | `docker compose up -d postgres redis` и ждать `healthy`; depends_on делает это сам, если стартовать целиком |
| `queue_declared … PRECONDITION_FAILED` | очередь существует с другими аргументами (quorum/classic, delivery-limit) | `docker compose exec rabbitmq rabbitmqctl delete_queue <name>` (после остановки воркеров), рестарт |
| `address already in use :8000` | подняты оба API-профиля (backend + gateway) | см. И-2 |
| `pull access denied / 401` для minio | образ minio/minio недоступен | `MINIO_IMAGE=bitnamilegacy/minio:latest` (уже дефолт) |
| rag_worker рестартится до загрузки модели | первая загрузка e5-small с HF — минуты | это не инцидент: `start_period: 300s`; проверить `docker compose logs rag_worker` на прогресс |

**Проверка.** `docker compose ps <svc>` → `healthy`; для API —
`curl -s http://localhost:8000/health`.

## И-2. 502 Bad Gateway от миниаппа/API

**Симптомы.** Сайт открывается, но каждый `/api/*` — 502; или API то
работает, то 502 (чередующиеся запросы).

**Диагностика.**

```bash
curl -s http://localhost:8000/health                 # сам API жив?
docker compose ps backend gateway                    # оба запущены = проблема
docker compose exec miniapp sh -c "wget -qO- http://backend:8000/health || echo FAIL"
```

**Причины и лечение.**

- **Оба API-профиля подняты** (backend + gateway имеют общий порт 8008→8000
  и сетевой алиас `backend`): DNS round-robin между монолитом и gateway →
  половина запросов 404/502. Лечение:
    ```bash
    docker compose rm -sf backend   # или gateway, смотря какой профиль нужен
    COMPOSE_PROFILES=micro docker compose up -d
    ```
- **API вообще не поднят / упал**: `docker compose up -d --build backend`
  (или gateway), затем миграции `alembic upgrade head`.
- **gateway подняли без доменных сервисов** (профиль micro): HTML отдаётся,
  `/api` — 502. Поднимать целиком: `docker compose --profile micro up -d`
  (или `start.bat micro`).
- **nginx закэшировал IP умершего backend** — по дизайну исключено
  (`resolver 127.0.0.11 valid=10s` в [frontend/nginx.conf](../frontend/nginx.conf));
  если всё же 502 — рестарт `docker compose restart miniapp`.
- **Стейл-образ**: роут есть в коде, контейнер отдаёт 404 —
  `docker compose --profile micro build <svc> && docker compose up -d <svc>`.

**Проверка.** `curl -s http://localhost:8000/health/detailed` — 200 и все
компоненты true; миниапп: загрузить ленту.

## И-3. Дрейф миграций (БД не соответствует коду)

**Симптомы.** `sqlalchemy.exc.ProgrammingError: relation/column … does not
exist` (или `already exists`) в backend/воркерах; `alembic upgrade head`
ничего не делает, а schema другая; два сервиса на одной БД с разными
версиями кода.

**Диагностика.**

```bash
docker compose exec -T backend alembic current         # версия схемы
docker compose exec -T backend alembic heads           # что требует код
ls app/infrastructure/db/migrations/versions | tail -5 # ожидаемая голова
docker compose exec postgres psql -U app -d autoselect -c \
  "SELECT version_num FROM alembic_version;"
```

**Лечение.**

- Код новее схемы: `docker compose exec -T backend alembic upgrade head`
  (в профиле micro — из `gateway`, он собран с alembic).
- Схема новее кода (откатили деплой): `alembic downgrade -1` на копии, в
  бою — поднять код до головы миграций (предпочтительно).
- Ручные правки схемы в обход alembic (главная причина «дрейфа»):
  сравнить метаданные и привести ревизию в соответствие. Разовая мера —
  `alembic stamp <rev>` (признать текущее состояние), но только убедившись,
  что схема действительно соответствует `<rev>`.
- Параллельные миграции RAG: у БД `rag` свои миграции
  (`rag_migrate_alembic`, `POSTGRES_DSN=…/rag`) — от основного alembic не
  зависят.

**Проверка.** `alembic current` == `alembic heads`; `/health` без 500;
`uv run pytest tests/catalog -q` на тестовой БД зелёный.

## И-4. Очередь копится (consumers растут messages, DLQ)

**Симптомы.** Глубина `fetch`/`send` растёт, `consumers = 0`; приходит
алерт «DLQ: N задач» (`dlq_alert`, порог `DLQ_ALERT_THRESHOLD=10`).

**Диагностика.**

```bash
docker compose exec rabbitmq rabbitmqctl list_queues name messages consumers
docker compose ps worker_fetch worker_send            # живы ли воркеры
docker compose logs worker_fetch | grep -Ei "error|task_to_dlq" | tail -30
docker compose exec rabbitmq rabbitmqctl list_connections user state | head
```

**Лечение.**

- **Воркер лежит**: `docker compose up -d worker_fetch` (heartbeat-liveness
  перезапускает сам при зависании event loop).
- **Источник лежит** (fetch падает по таймауту): circuit breaker сам
  отключит источник после `SOURCE_CB_ERROR_STREAK` ошибок — очередь
  перестанет расти. Долгие encar-задачи упираются в `job_timeout=360`
  (fetch) — это ретраится и уходит в DLQ после 3 попыток.
- **Разбор DLQ**: посмотреть тело сообщений (Management UI :15672 →
  dead_letter → Get Message), исправить причину, затем
  ```bash
  docker compose exec -T backend python -m app.infrastructure.messaging.replay --limit 100 --dry-run
  docker compose exec -T backend python -m app.infrastructure.messaging.replay --limit 100
  ```
  Битые (недекодируемые) replay удаляет с логом. Мусор в DLQ без ценности:
  `docker compose exec rabbitmq rabbitmqctl purge_queue dead_letter`.
- **RabbitMQ весь лежит**: HTTP-ядро не зависит (`required: false`),
  проверить `docker compose logs rabbitmq`, рестарт; диск-full на брокере
  ставит Publishers в блокировку — проверить `df -h` на хосте.

**Проверка.** `rabbitmqctl list_queues` — messages убывают, consumers = 1–2
у рабочих очередей; `/api/admin/sources` — error_streak не растёт.

## И-5. Centrifugo: клиенты не подключаются / «unknown channel»

**Симптомы.** Чаты не обновляются в реальном времени (едут поллингом
`after_id`), в логах centrifugo `unknown channel` / `namespace not found`;
publish из бэкенда логирует warning.

**Диагностика.**

```bash
docker compose logs centrifugo | grep -i "unknown\|namespace" | tail
docker compose logs centrifugo | head -20          # чем подменился config (sed)
docker compose exec centrifugo sh -c "cat /tmp/config.json"   # итоговый конфиг
curl -s http://localhost:8000/api/chats/ws-token -H "Authorization: Bearer <jwt>"  # выдаётся ли токен
```

**Причины и лечение.**

- **Нет namespace канала**: разрешены только `user:` / `partner:` /
  `thread:` ([configs/centrifugo.json.tmpl](../configs/centrifugo.json.tmpl)).
  Новый тип канала → добавить namespace и `subscribe_proxy_enabled`,
  рестарт `docker compose --profile realtime up -d centrifugo`.
- **Секреты разошлись**: connection-JWT подписан `CENTRIFUGO_SECRET`
  бэкенда, а centrifugo проверяет `__SECRET__` из той же env — если
  меняли ключ, рестартовать ОБА (`centrifugo` и `backend`/`chat`).
- **`__PROXY__` указывает не туда**: sed подставляет
  `http://gateway:8000`; в mono-профиле alias `backend` принадлежит
  backend-контейнеру — это работает, только если один из них запущен.
- **не инцидент ядра**: publish-ошибки логируются warning, REST чатов
  работает, клиенты — на поллинг-фоллбэке (5 с). Восстановление
  centrifugo само возвращает realtime без действий клиента.

**Проверка.** Открыть чат в двух окнах: сообщение появляется без
перезагрузки; `GET /api/chats/online?ids=...` возвращает id онлайн-юзеров.

## И-6. Фото не зеркалятся / 503 photos_unavailable

**Симптомы.** В карточках внешних лотов битые изображения; `POST
/api/market/photos` отвечает 503 `photos_unavailable`; в логах
`mirror_task_failed` / `photos_ensure_bucket_failed`.

**Диагностика.**

```bash
docker compose ps minio                          # здоров?
curl -s http://localhost:9000/minio/health/live  # 200
docker compose logs worker_fetch | grep -i mirror | tail
docker compose logs worker_fetch | grep -i bucket | tail
docker run --rm --network auto_bot_default minio/mc sh -c \
  "mc alias set local http://minio:9000 minioadmin minioadmin && mc ls local/"
```

**Причины и лечение.**

- **MinIO не поднят** (профиль storage): `docker compose --profile storage up -d minio`.
- **Креды**: `S3_ACCESS_KEY`/`S3_SECRET_KEY` должны совпадать с
  `MINIO_ROOT_USER`/`MINIO_ROOT_PASSWORD` (`minioadmin` в dev).
- **`S3_ENDPOINT_URL` пуст** → storage выключен: задать (`http://minio:9000`
  в compose), рестарт backend + worker_fetch.
- **Volume создан другим образом (root)**: разово
  `docker run --rm -v auto_bot_minio_data:/data alpine chown -R 1001:1001 /data`
  (bitnilegacy-образ — uid 1001; комментарий в docker-compose.yml).
- **Зеркало падает на конкретном лоте**: mirror_photos_task — best-effort,
  частичные загрузки откатываются (`delete_lot_mirror`), лот остаётся с
  внешними URL — не инцидент, следующий прогон долбит заново. Проверить
  руками: `curl -sI <внешний url фото>` — источник мог начать отдавать 403.
- Bucket создаётся лениво, policy public-read идемпотентна — отдельного
  шага создания не требуется.

**Проверка.** `mc ls local/market-photos/mirror/encar/<id>/` показывает
`000.jpg…004.jpg`; карточка лота грузит фото с `http://localhost:9000/market-photos/...`.

## И-7. Перевод: 429 / переводы не появляются

**Симптомы.** `title_ru` остаётся NULL; в логах fetch-воркера
`translate_all_failed` / HTTP 429 от сервиса перевода; translate
рестартится по RSS-сторожу.

**Диагностика.**

```bash
curl -s http://localhost:8200/health
docker compose logs translate | grep -Ei "429|rss|error" | tail -20
docker compose logs worker_fetch | grep -i translate | tail
docker compose exec postgres psql -U app -d autoselect -c \
  "SELECT source, count(*) FILTER (WHERE title_ru IS NOT NULL) AS ru, count(*) AS total FROM listings GROUP BY 1;"
```

**Причины и лечение.**

- **429 от Bot API/LLM внутри цепочки**: после 3 ошибок circuit breaker
  (`TRANSLATE_CB_FAIL_THRESHOLD=3`) открывается на `TRANSLATE_CB_COOLDOWN_SEC`
  (300 с) — обычно самозакрывается; алерт приходит при открытии CB.
- **Сервис не включён**: `TRANSLATE_ENABLED=true` и
  `TRANSLATE_SERVICE_URL=http://translate:8200` в `.env`, рестарт backend +
  воркеров. Токены должны совпадать: `TRANSLATE_API_TOKEN` (backend) ==
  `API_TOKEN` (контейнер translate).
- **Таймауты CPU-инференса**: чанки enqueue уже ограничены
  (16 ids/задача, `job_timeout` 120 с у send-очереди); при регулярных
  таймаутах снизить `TRANSLATE_BATCH_SIZE`, поднять `TRANSLATE_TIMEOUT_SEC`.
- **RSS-рестарты** (`translate_rss_limit_exceeded` — самовыход, docker
  рестартит): убедиться, что `TRANSLATE_MAX_RSS_MB` (2950) выше пика
  (~2.9 ГБ на батче 32) и mem_limit 3500m не занижен. Зацикленные
  рестарты = порог ниже пика.
- Перевод некритичен: выключен флагом/упал → UI показывает оригинальный
  тайтл; `translate_all_failed` (0 из N) — исключение, задача ретраится.

**Проверка.** Доля `title_ru` растёт после свежего fetch; в логах
`translate_done: source=… translated=N`.

## И-8. Оплата Stars прошла, подписка/лот не активировались

**Симптомы.** Пользователь заплатил (списание XTR видно у юзера), тариф
не включился / лот не продлился; в логах бота
`payment_activation_failed_after_retries`.

**Диагностика.**

```bash
docker compose logs bot | grep -iE "successful_payment|payment_activation" | tail
docker compose logs backend | grep -i "subscription_activated\|placement_extended" | tail
docker compose exec postgres psql -U app -d autoselect -c \
  "SELECT id, user_id, status, kind, tier, period_days, provider_payment_id FROM payments ORDER BY id DESC LIMIT 10;"
```

**Причины и лечение.**

- **Вебхук вообще не приходил**: бот жив? (`docker compose logs bot`,
  heartbeat). Бот получает `successful_payment` по long polling; если бот
  был лежал в момент оплаты — Telegram ретраивает update сам; при потере
  запись остаётся `pending` — активировать вручную (ниже).
- **Backend был недоступен**: бот ретраит `payment_success` 3 раза
  (бэкофф 2/4 с); после исчерпания — алерт админу. Ручная доактивация:
  ```sql
  -- убедиться, что provider_payment_id уникален и списание было
  UPDATE payments SET status='paid', provider_payment_id='<charge_id>', paid_at=now() WHERE id=<id>;
  -- затем применить эффект сценарием или руками:
  UPDATE users SET subscription_tier='<tier>', subscription_until=now()+'<N days>' WHERE telegram_id=<uid>;
  -- (для kind='placement': UPDATE listings SET placement_paid_until=... )
  ```
- **Ретрай вебхука (не двойная активация!)**: `ConfirmPayment`
  идемпотентен — повторный вызов с `status='paid'` не продлевает второй
  раз; `mark_paid` — условный UPDATE, параллельный вебхук получает False
  и откатывается. Двойных списаний/продлений быть не должно — если
  продлилось дважды, это баг, заводить INCIDENTS.
- **payload не прошёл pre_checkout** (`pre_checkout_bad_payload`):
  пользователь видел «Счёт устарел» и платёж не проводился — денег нет,
  ничего чинить не надо.

**Проверка.** `payments.status='paid'` у платежа; юзер видит тариф в
`GET /api/me`; повторный `successful_payment` (симуляция ретрая) не меняет
`subscription_until`.

## И-9. Флейк-тесты (CI/локально)

### vitest: OOM / RangeError

**Симптомы.** `npx vitest run` падает целиком или частью файлов:
`FATAL ERROR: Reached heap limit` / `RangeError: Array buffer allocation
failed` / воркеры умирают, не стартовав.

**Причина.** 44 тестовых файла, tinypool с дефолтами плодит воркеров по
числу ядер — на этой машине память кончается (PROGRESS.md:
«threads=2: minThreads=1 обязателен — tinypool конфликтует с дефолтной min»).

**Диагностика/лечение.** Запускать строго с явным пулом:

```bash
cd frontend && npx vitest run --pool=threads \
  --poolOptions.threads.minThreads=1 --poolOptions.threads.maxThreads=2
```

**Проверка.** 552/552 зелёные (44 файла). Один файл:
`npx vitest run src/screens/Feed.test.tsx --pool=threads --poolOptions.threads.minThreads=1 --poolOptions.threads.maxThreads=2`.

### pytest: `tests/dialog/test_rag.py::test_answer_question_without_rag_uses_llm_answer`

**Симптом.** В полном прогоне падает; изолированный прогон зелёный.

**Причина.** Тест передаёт `rag=None` в `run_agent`; если в локальном
`.env` задан `RAG_SERVICE_URL` и включён RAG, агент создаёт реальный
shared-клиент (`get_shared_rag_client`, [app/infrastructure/ai/agent.py](../app/infrastructure/ai/agent.py))
и тул `answer_question` ходит в живой RAG на `localhost:8100`: когда
стек поднят, RAG отвечает своим текстом (confidence ≥ 0.6) и assert на
ответ LLM падает. Изолированно (без сервиса на 8100) RAG недоступен →
fallback на LLM-ответ → тест зелёный.

**Диагностика/изоляция.**

```bash
# зелёный изолированный прогон:
uv run pytest tests/dialog/test_rag.py -q
# воспроизведение флейка при поднятом ai-профиле:
docker compose --profile ai up -d rag_api && uv run pytest tests/dialog/test_rag.py -q
# убрать влияние окружения:
RAG_SERVICE_URL= RAG_ENABLED=false uv run pytest tests/dialog/test_rag.py -q
```

**Лечение (окружение, не код):** гонять полный прогон без живого RAG
(`RAG_SERVICE_URL=` в окружении тестов) или добавить фикстуру,
пинующую `rag_client`. Не блокер: RAG-контур деградирует в LLM в бою
по design'у.

**Проверка.** Два последовательных полных прогона `uv run pytest -q`
стабильны.

---

# Регулярные операции

## Р-1. Ротация API-ключей партнёров

Ключи `pi_…` (24 b64url-символа, 192 бита энтропии); в БД — только
SHA-256 ([app/infrastructure/security/partner_keys.py](../app/infrastructure/security/partner_keys.py)).
Сырой ключ показывается один раз.

```bash
# ротация (старый ключ гаснет сразу, новый выдаётся в ответе):
curl -s -X POST http://localhost:8000/api/admin/partners/<partner_id>/rotate \
  -H "X-Internal-Token: <secret>"
# отзыв всех ключей (партнёр теряет доступ к API):
curl -s -X POST http://localhost:8000/api/admin/partners/<partner_id>/revoke \
  -H "X-Internal-Token: <secret>"
```

После ротации: передать новый ключ партнёру по защищённому каналу;
проверить, что старый отклоняется (401 `X-API-Key`) и новый проходит
rate-limit окно (`PARTNER_RATE_LIMIT_PER_MIN`). Регламент: ротация при
уходе сотрудника/подозрении на утечку + плановая раз в квартал.

## Р-2. Чистка stale-лотов

Отработана автоматически cron'ом `stale_check_job` (04:00 UTC,
fetch-воркер): лоты, не виданные `STALE_MAX_AGE_DAYS` (3) дней →
`is_active=false`; CB-источники пропускаются; исчезнувшие внешние лоты
чистятся из S3 (зеркало + raw), ставится «gone»-скан уведомлений.

Ручной прогон / аудит:

```bash
# сколько кандидатов на деактивацию:
docker compose exec postgres psql -U app -d autoselect -c \
  "SELECT source, count(*) FROM listings WHERE is_active AND parsed_at < now() - interval '3 days' GROUP BY 1;"
# ручной запуск задачи (через очередь):
docker compose exec -T backend python - <<'PY'
import asyncio
from app.infrastructure.messaging.legacy_worker import enqueue_task
asyncio.run(enqueue_task("stale_check_job"))
PY
# реактивировать ошибочно погашенные (после разбора):
docker compose exec postgres psql -U app -d autoselect -c \
  "UPDATE listings SET is_active=true WHERE id IN (<ids>);"
```

Проверка: лог fetch-воркера `stale_check_done: gone=N`; `mirror/` префиксы
удалённых лотов исчезли из minio.

## Р-3. Пересчёт/аудит подписок

Ежедневно 12:00 UTC `subscription_check_job` (send-воркер): напоминания
за 3/1 день, даунгрейд на free после окончания (не позднее 3 дней),
`subscription_until` очищается. Бессрочный партнёрский Business
(`subscription_until IS NULL`) не даунгрейдится.

Аудит руками:

```bash
docker compose exec postgres psql -U app -d autoselect -c \
  "SELECT subscription_tier, count(*), min(subscription_until), max(subscription_until) \
     FROM users WHERE is_active GROUP BY 1 ORDER BY 1;"
# кто просрочен, но ещё не даунгрейднут (джоб ещё не прошёл):
docker compose exec postgres psql -U app -d autoselect -c \
  "SELECT telegram_id, subscription_tier, subscription_until FROM users \
     WHERE subscription_tier <> 'free' AND subscription_until < now();"
# ручной прогон:
docker compose exec -T backend python -c "import asyncio; \
 from app.infrastructure.messaging.legacy_worker import enqueue_task; \
 asyncio.run(enqueue_task('subscription_check_job'))"
```

Ручная правка одного юзера: `UPDATE users SET subscription_tier='pro',
subscription_until=now()+interval '30 days' WHERE telegram_id=<id>;`
— и убедиться, что кэш квот в Redis не держит старый тариф
(`DEL` по префиксу при необходимости).

## Р-4. Дамп/анализ таблиц

```bash
# структура+данные одной таблицы:
docker compose exec -T postgres pg_dump -U app -d autoselect -t listings \
  --data-only | gzip > listings_$(date +%F).sql.gz
# аналитика по таблицам (размеры, строки):
docker compose exec postgres psql -U app -d autoselect -c \
  "SELECT relname, n_live_tup, pg_size_pretty(pg_total_relation_size(relid)) \
     FROM pg_stat_user_tables ORDER BY pg_total_relation_size(relid) DESC LIMIT 15;"
# медленные/долгие транзакции:
docker compose exec postgres psql -U app -d autoselect -c \
  "SELECT pid, now()-xact_start AS age, state, left(query,80) FROM pg_stat_activity \
     WHERE xact_start IS NOT NULL ORDER BY age DESC LIMIT 10;"
# дедуп-аудит лидов / событий за сутки:
docker compose exec postgres psql -U app -d autoselect -c \
  "SELECT status, count(*) FROM leads WHERE created_at > now()-interval '1 day' GROUP BY 1;"
# сырой payload лота (если RAW_PAYLOAD_ENABLED):
curl -s -H "X-Internal-Token: <secret>" \
  "http://localhost:8000/api/admin/listings/<id>/raw"   # presigned URL на 10 мин
```

Основные таблицы: `users`, `listings`, `price_analysis`, `price_history`,
`favorites`, `filters` (search_filters), `leads`, `payments`, `chats`,
`chat_messages`, `market_lots`, `partner_accounts`, `sent_log`,
`analytics_events`, `source_health`, `admin_actions`, `translations`,
`inspection_reports`, `outbox`.

## Р-5. Ручной fetch источника / здоровье парсеров

```bash
curl -s http://localhost:8000/api/admin/sources -H "X-Internal-Token: <secret>" | jq
curl -s -X POST http://localhost:8000/api/admin/sources/encar/fetch \
  -H "X-Internal-Token: <secret>"
docker compose logs worker_fetch | grep -E "fetch_failed|source_skipped_circuit_breaker"
```

Circuit breaker пропускает упавший источник сам; ParserDoctor (fire при
0 карточек) чинит селекторы в `suggest`/`auto` — проверить его логи в
`docker compose logs worker_fetch | grep -i doctor`.
