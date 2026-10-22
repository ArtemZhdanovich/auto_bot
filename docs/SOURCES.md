# Источники, парсинг и воркеры

## Адаптеры (`app/infrastructure/sources`)

Каждый источник реализует контракт `BaseAdapter`: `capabilities()`,
`fetch(criteria, page)` → список `UnifiedListing`.

| Адаптер | Источник | Нюансы |
|---|---|---|
| `EncarAdapter` | encar.com (HTML) | CSS-селекторы в конфиге БД → чинятся без деплоя (и ParserDoctor'ом) |
| `KCarApiAdapter` | kcar.com Search API | `{totalCnt, list[]}`; цены в манах × `price_unit` (10 000 → KRW); пагинация с 1-й страницы |
| `Che168Adapter` | che168.com (HTML, Китай) | Цены в 万 (10k CNY) → нормализация; гео-блокировки → прокси DataImpulse `GEO=cn` |

**Общая обвязка (`sources/common/`)**:
- `http.py` — httpx-клиент с таймаутами, ретраями, UA;
- `normalize.py` — нормализация марок/моделей/топлива/КПП → единый словарь;
- `dedup.py` — 2 уровня: БД (уникальный `(source, source_id)`, content_hash) +
  Redis (VIN/fuzzy между источниками);
- `proxy.py` — `GatewayProxyPool` (DataImpulse): sticky-сессии, CB при ошибках;
  формат логина `login__cr.<geo>` — гео выхода (`cn` для che168).

**Registry**: `ADAPTER_CLASSES` → инстансы по enabled-источникам из таблицы
`sources` (name, priority, capabilities, config JSON). Источник
глушится/включается в БД, приоритет — порядок в ленте.

## Пайплайн сохранения (`app/application/listings/ingest.py`)

1. Дедуп внутри источника (`source, source_id`).
2. NEW или **изменившийся content_hash** → UPDATE (снимок в историю цен
   `listing_price_history`; PK `(id, recorded_at)` — подготовка к
   партиционированию, миграция 0013).
3. Смена хэша/новое → задача `translate_listings` (если включен перевод)
   и upload raw JSON в S3 (если включен; ошибка S3 не ломает сохранение —
   `raw_key = NULL`).
4. Совпадения с фильтрами → постановка в очередь `send`.

## Воркеры (`app/infrastructure/workers`)

**Fetch Worker** (`python -m app.infrastructure.workers.fetch`), очередь
`fetch`: задачи `fetch_source` (парсинг источника), `translate_listings`;
cron: `raw_retention_job` 03:30 UTC (retention MinIO), `stale_check_job`
04:00 UTC (is_active=false для объявлений, которых нет ≥ 3 дней;
CB-источники пропускаются).

**Send Worker** (`python -m app.infrastructure.workers.send`), очередь `send`:
задачи `send_listing_notification`, `send_listing_status`,
`notify_new_listings`, `listing_status_scan`; cron: `digest_job` 09:00 и
20:00 UTC, `subscription_check_job` 12:00 UTC.

**FastStream-контур**: команды (`app/transport/messaging/contracts.py`) —
строгие Pydantic-модели с обязательным `idempotency_key`; обработчик
`enrich` догружает спеки лота из detail-источника; постановка — через
publisher, доставка — outbox-релей с идемпотентностью
(`app/infrastructure/messaging/`).

**Перед отправкой в Telegram**:
1. **Token Bucket** (Redis+Lua): ~30 msg/s глобально, ~1 msg/s на чат;
   нет слота → sleep до 3 попыток; Redis лежит → fail-open.
2. 429 от Telegram → `retry_after` → sleep → 1 повтор.
3. Персональный rate limit 30 с/юзера + антидубль 7 дней — до постановки
   в очередь.

## Сырые ответы в MinIO (raw_payload)

- `RAW_PAYLOAD_ENABLED` (по умолчанию выкл); bucket `raw-payloads`,
  retention 180 дней (cron-очистка), presigned GET 10 минут — карточка сырого
  ответа доступна админу (`GET /api/admin/listings/{id}/raw`).
- Загрузка только для NEW/изменившего хэш — бесплатная дедупликация
  по префиксу.
