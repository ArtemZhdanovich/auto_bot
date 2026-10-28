# Источники, парсинг, S3-зеркало и перевод

## Адаптеры (`app/infrastructure/sources`)

- **encar** (`encar.py`): list-API + detail-enrichment (спеки, чек
  осмотра: inspect/diagnosis/record-open + классический mdsl-чек),
  перевод хангыля → русский через NLLB (`common/translate.py`: глоссарий
  → микросервис, батчи ≤48 — лимит API); чеки и история ДТП попадают
  в `inspection_reports`.
- **kcar** (`kcar.py`): зашифрованный POST (AES-CBC из бандла),
  без пагинации (top-100), перевод тех же полей.
- **che168**: адаптер описан, источник выключен в БД.
- Прокси-пул (DataImpulse) для гео-блокированных источников;
  credentials/config в таблице `sources`, health/circuit-breaker.

## Воркеры

- **fetch** (`workers/fetch.py`): cron `schedule_fetch_due` (минута)
  → задачи `fetch_source_task` (адаптер → `save_listings` CTE-upsert +
  история цен + кросс-дедуп Redis → события price_drop/returned →
  translate-задачи). Фоновая `mirror_photos_task` (см. ниже).
  `stale_check_job` (04:00 UTC): пропавшие лоты → is_active=false,
  disappeared_at + **S3-cleanup** их зеркал и raw; `raw_retention_job`.
- **send** (`workers/send.py`): instant-уведомления (только
  Business-фильтры — `notify.effective_channel`), дайджесты по
  тарифу (free — утро понедельника, pro — утро+вечер), подписочные
  напоминания/даунгрейд, экспирация размещений (status='expired'),
  rate-limits Telegram.

## S3-зеркалирование фото (порядок гарантирован)

- Новые/обновлённые внешние лоты → `mirror_photos_task` (батч 40):
  первые `MARKET_PHOTO_MIRROR_LIMIT` (5) фото качаются в MinIO
  `market-photos` с ключом `mirror/{source}/{source_id}/{NNN}.jpg` —
  индекс в ключе = порядок; полный успех → `listings.images` = наши
  URL; частичный сбой → откат зеркала (порядок не ломаем); уже
  зеркаленные skip (head prefix).
- Лот исчез с источника → stale-check удаляет
  `delete_lot_mirror(source, source_id)` + raw-объект (`raw_key`).
- Owner-лоты (рынок) — пользовательский контент, не трогаем.

## Перевод

Микросервис `services/translate` (NLLB-200, aioboto3 кэш Redis):
`POST /translate {texts[], source_lang, target_lang}`. Глоссарий
`domain/translation/glossary` канонизирует бренды/модели/термины
(включая термины чеков: 교환→замена и т.п.) до модели; сбои не роняют
fetch (остаётся оригинал). Чаты дополнительно маскируют контакты
(`domain/shared/contacts`) — в seller/deal/group.
