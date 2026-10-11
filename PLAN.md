# PLAN: Отслеживание объявлений (цены, наличие) + стратегия хранения

Согласовано 2026-10-11. Продуктовое ядро — фазы 1–4; хранение/архивация — 6–7.

---

## 1. Что НЕ делаем и почему (ревью планов, 5 находок)

1. **Не партиционируем `listings`** (по `first_seen_at` или как-либо ещё).
   Уникальный индекс партиционированной таблицы обязан включать ключ партиции,
   а `first_seen_at` при каждом парсинге свой → ломается дедуп
   `ON CONFLICT (source, source_id)` → дубли строк. Собственная оценка
   объёмов (125k строк / ~4 ГБ за 2 года) партиции не оправдывает. Если
   партиционировать что-то — то `listing_price_history` по `recorded_at`
   (монотонный рост), и то при миллионах строк.
2. **Не используем `seen_ids`/`mark_missing` для исчезновений.** Fetch идёт
   по критериям и `max_pages=3` → выборка частичная; SQL `source_id != ALL($2)`
   пометил бы «исчезнувшими» все объявления вне фильтра. Только stale-подход:
   `is_active = false` по `last_seen_at < now() - N дней`.
3. **Не переводим `description`.** Поля нет в `UnifiedListing`; translation
   MVP — title-only. Расширение контракта — отдельно, с адаптерами.
4. **Не строим уведомления рядом с существующими.** Контур уже есть:
   `notify.py` + очередь `notify_new_listings` + `sent_log` (антидубль по
   `(user_id, listing_id)`) + Redis `notified:{uid}:{lid}` TTL 7д + тихие часы.
   Price-drop/gone/returned ложатся на него (в фазе 3 добавить `kind`).
5. **Нумерация миграций с `0009`.** 0007/0008 заняты. Переводные колонки
   (`title_raw/title_ru`, `source_language`, `translated_at`, …) — отдельной
   миграцией `0011_translations` в фазе 5, НЕ в 0009 (номер 0010 занят
   `sent_log.kind`, фаза 3).

Дополнения ревью:
- **`content_hash` — единый триггер.** Смена хэша = «объявление изменилось»;
  тот же хэш в фазе 5 запускает повторный перевод. Двух механизмов детекции
  не заводить.
- **`mark_stale` уважает circuit breaker.** Если источник в CB
  (`source_health.error_streak >= settings.source_cb_error_streak`), его
  объявления не помечаем исчезнувшими — источник лежит, они не могли видеться.

---

## 2. Порядок фаз

| # | Что | Оценка | Зависимости |
|---|---|---|---|
| 1 | Миграция `0009_listing_tracking` + ORM | 1 день | — |
| 2 | `compute_content_hash` + upsert `save_listings` (CTE) | 2 дня | фаза 1 |
| 3 | ✅ Stale-cron (с проверкой CB) + уведомления price_drop/gone/returned, `sent_log.kind` | 2–3 дня | фаза 2 |
| 4 | ✅ История цен в UI (карточка, бейджи избранного) | 1 день | фаза 2 |
| 5 | ✅ Перевод: `0011_translations`, title-only, NLLB (`services/translate`) | 3–4 дня | фаза 2 |
| 6 | ✅ `raw_payload` → S3 (MinIO) + retention-крон | 1–2 дня | — |
| 7 | Партиционирование `listing_price_history` (не `listings`!) | отложить | миллионы строк |

---

## 3. Схема данных (фазы 1–2, миграция `0009`)

```sql
ALTER TABLE listings
  ADD COLUMN content_hash VARCHAR(64),
  ADD COLUMN is_active BOOLEAN NOT NULL DEFAULT TRUE,
  ADD COLUMN first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  ADD COLUMN last_seen_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  ADD COLUMN disappeared_at TIMESTAMPTZ,
  ADD COLUMN price_changed_at TIMESTAMPTZ;
-- backfill: first_seen_at = last_seen_at = COALESCE(parsed_at, now())

CREATE TABLE listing_price_history (
    id BIGSERIAL PRIMARY KEY,
    listing_id BIGINT NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
    price NUMERIC NOT NULL,
    currency VARCHAR(8),
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_listings_active ON listings(is_active) WHERE is_active = TRUE;
CREATE INDEX idx_listings_last_seen ON listings(last_seen_at);
CREATE INDEX idx_listings_first_seen_brin ON listings USING BRIN (first_seen_at);
CREATE INDEX idx_price_history_listing
    ON listing_price_history(listing_id, recorded_at DESC);
```

Существующие `uq_listings_source_source_id` (table constraint) и индексы
make_model/year/price/vin не трогаем.

### Upsert: CTE (1 запрос, старое + новое)

`INSERT ... ON CONFLICT DO UPDATE ... RETURNING` возвращает **новые** значения,
поэтому old_price/old_currency/old_hash/was_active достаём CTE:

```sql
WITH existing AS (
    SELECT id, price, currency, content_hash, is_active FROM listings
    WHERE source = :source AND source_id = :source_id FOR UPDATE
),
upserted AS (
    INSERT INTO listings (...) VALUES (...)
    ON CONFLICT ON CONSTRAINT uq_listings_source_source_id DO UPDATE SET ...
    RETURNING id
)
SELECT e.id AS old_id, e.price AS old_price, e.currency AS old_currency,
       e.content_hash AS old_hash, e.is_active AS was_active, u.id AS listing_id
FROM upserted u LEFT JOIN existing e ON TRUE;  -- LEFT JOIN: строка есть и у NEW
```

Правила DO UPDATE SET (критично):
- **`first_seen_at` НЕ в SET** — иначе сброс при каждом парсинге;
- **`disappeared_at = NULL`** безусловно — реактивация возвращённого
  объявления (у активного он и так NULL);
- `is_active = TRUE`, `updated_at = now()`; остальное — `EXCLUDED.*`.

Известная гонка: при конкурентном NEW двух воркеров у проигравшего `old_*`
будут NULL (его CTE увидел пустую `existing`). Для MVP допустимо; если
станет критично — `SELECT ... FOR UPDATE` вне CTE (+1 round-trip).

`bindparam(type_=JSONB)` нужен только для параметров `VALUES` (`images`,
`raw_payload`); в `SET ... = EXCLUDED.*` типизация уже есть.

### Точка в `listing_price_history`

- NEW → первая точка (если цена известна);
- иначе → только если `price is not None` и пара `(price, currency)`
  изменилась относительно old (старая цена NULL после гонки → точку пишем
  как новую базу).

---

## 4. Ключевые сигнатуры (без реализации)

```python
def compute_content_hash(item: UnifiedListing) -> str:
    """sha256(title|price|currency|mileage_km|sorted(images)).

    Без parsed_at/updated_at. Смена хэша = «объявление изменилось»
    и в фазе 5 — триггер повторного перевода.
    """

async def save_listings(session, redis, items) -> dict[str, int]:
    """Cross-dup (Redis) ДО upsertа; CTE-upsert по карточке (1 запрос);
    точка цены при изменении.

    {'new', 'updated', 'unchanged', 'price_changed', 'reactivated', 'cross_dup'}
    """

async def mark_stale(session, *, max_age_days: int | None = None) -> list[int]:
    """is_active=false + disappeared_at для last_seen_at старше порога
    (STALE_MAX_AGE_DAYS); источники в circuit breaker пропускаются.
    Возвращает id деактивированных (для «gone»-уведомлений). Фаза 3."""
```

Fetch-воркер ставит `notify_new_listings` при `new + price_changed +
reactivated` (скан дешёвый, `sent_log`/Redis-дедуп отфильтрует повторы).

---

## 5. Открытые вопросы (решить при наступлении фазы)

- **Фаза 3 (UX):** РЕШЕНО: `reactivated` → отдельный kind `returned`
  («снова в продаже»); аудитория статусных уведомлений — пользователи,
  уже получавшие карточку объявления (sent_log).
- **Фаза 5:** РЕШЕНО: NLLB — отдельный optional-микросервис
  `services/translate` (FastAPI + nllb-200-distilled-600M), НЕ в RAG-сервисе:
  изоляция отказа (перевод некритичен, RAG «не всегда поднят»), изоляция
  памяти (~2 ГБ не в I/O-bound fetch-воркере), независимый деплой/своп модели.
  Бэкенд: `TRANSLATE_ENABLED` + `TranslateClient` (батчи, Redis-кэш 30 дней,
  retry, circuit breaker, fallback на оригинал).
- **Фаза 5:** РЕШЕНО: `description` НЕ заводим — translation MVP title-only
  (`title_raw`/`title_ru` в `0011_translations`); расширение контракта —
  отдельно, с адаптерами.
- **Фаза 6:** РЕШЕНО: **aioboto3** (не boto3) — fetch/send воркеры асинхронные,
  синхронный boto3 блокировал бы event loop. `S3Storage`
  (app/services/storage.py): ключ `raw/{source}/{yyyy}/{mm}/{id}_{hash}.json`
  (retention по префиксу + дедуп по хэшу), S3 Metadata (source/listing_id/
  content_hash/fetched_at), botocore retries+timeouts, флаг
  `RAW_PAYLOAD_ENABLED`, fallback: MinIO недоступен → объявления сохраняются,
  `raw_key` = NULL. Админке — presigned GET (`GET /api/admin/listings/{id}/raw`,
  10 мин), прямого доступа к MinIO нет. Retention-крон 03:30 UTC
  (`raw_retention_job`, `RAW_RETENTION_DAYS=180`).
- **Опционально:** перевод индексов `listings` в partial-версии
  (`WHERE is_active`) — мерить на реальных объёмах.

## 6. Фаза 7 — партиционирование `listing_price_history` (когда «пора»)

Триггер: таблица стабильно > 1–2 млн строк и запросы динамики цены сканируют
слишком много. Раньше — не трогать (overhead на вставку, сложнее миграции).

Когда придёт время:
- **RANGE по `recorded_at`** (месяц/неделя); LIST/HASH не подходят для time-series.
- **Ключ партиционирования обязан входить в каждый уникальный индекс**:
  PK сделать `(listing_id, recorded_at)` — заранее спроектировать схему так,
  чтобы миграция была `ATTACH`, а не перестройкой.
- **pg_partman** для автосоздания партиций (иначе вставки упадут на границе месяца).
- **Retention**: старые партиции (12–24 мес) — `DROP TABLE` (мгновенно) или
  архив в S3 (MinIO из фазы 6 — связка «горячее/холодное»).
- Альтернатива при аналитике/агрегатах — TimescaleDB (чанки, сжатие,
  continuous aggregates); для MVP не нужно.
