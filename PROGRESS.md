

## 65. Инфра: healthcheck всем контейнерам + OOM-защита translate + лок ParserDoctor + PDP «все данные» (2026-10-17)

- **Запуск рантайма (перевод + RAG + постоянный скрининг):** подняты qdrant,
  minio, translate, rag_migrate/rag_worker/rag_api рядом с backend/bot/miniapp.
- **Утечки памяти NLLB (фидбек «следите за контейнером translate»):** тройная
  защита — (1) чанкинг батчей (TRANSLATE_CHUNK=16) + `gc.collect()` после чанка
  в `nllb.py`; (2) RSS-сторож `_rss_watchdog` в `main.py` (раз в 60с меряет
  VmRSS, при > TRANSLATE_MAX_RSS_MB=2600 делает `os._exit(1)` → рестарт с
  прогревом ~30с, бэкенд тем временем fallback'ом отдаёт оригинальные тайтлы);
  (3) жёсткий `mem_limit: 3g` / `memswap_limit: 3g` в compose (иначе oom-killer
  может убить соседей). Плюс `OMP_NUM_THREADS=2`, `TOKENIZERS_PARALLELISM=false`.
- **ParserDoctor: лок на источник (фидбек «не жечь LLM-токены дублями»):**
  к cooldown 6ч добавлен running-лок `doctor:running:{src}` (TTL 30 мин,
  ставится перед LLM-запросом, снимается в finally; падение процесса чистит
  TTL). Параллельный воркер получает `already_running` и второй GLM-запрос
  не шлёт. `redis=None` теперь логируется предупреждением. Тело ремонта
  вынесено в `_repair_with_llm`. Тесты +2 (already_running блокирует LLM,
  лок снимается после ремонта, cooldown остаётся).
- **Healthcheck у каждого контейнера:** добавлены недостающие — bot,
  worker_fetch, worker_send (long-polling/консюмеры без HTTP-порта → новый
  `app/services/heartbeat.py`: фоновая задача пишет heartbeat-файл каждые 15с,
  `python -m app.services.heartbeat <name>` в healthcheck считает живым файл
  не старше 90с — зависший event loop → unhealthy), qdrant и minio
  (TCP-проба через bash /dev/tcp — wget/curl в образах нет), miniapp (nginx
  busybox wget), rag_api (`/health`). one-shot rag_migrate* — healthcheck не
  применим (exit 0 по дизайну).
- **RAM-лимиты:** qdrant 2g, rag_worker 1500m (embedding + шифрованные чанки),
  translate 3g — «сколько дадут» заменено фиксированными потолками.
- **PDP «все данные, что есть»:** `ListingOut` + updated_at/last_seen_at/
  price_changed_at (source_language уже был); карточка Listing.tsx — блок
  «Характеристики» (кузов/год/объём/топливо/КПП/привод/пробег/цвет/VIN,
  каждая строка с пояснением `info-row__hint`, пустые не рендерим) и блок
  «Данные объявления» (статус «в продаже/снято», источник+ID, в базе с,
  обновлено источником, цена менялась, последняя проверка); название с
  источника — заголовком, оригинал + язык под переводом. CSS: `.info-row--stacked`,
  `.info-row__main/__hint`, `.spec-group__title`.
- **Докерфайлы:** rag — `COPY pyproject.toml uv.lock` (uv.lock не копировался —
  билд падал), torch переведён на CPU-индекс (`[[tool.uv.index]] pytorch-cpu` +
  `tool.uv.sources` + torch как прямая deps: иначе тянулись ~3-5ГБ nvidia-*),
  uv.lock перегенерирован (nvidia-пакетов 0); translate/rag — retry-циклы
  (5 попыток) + BuildKit cache-маунты pip/uv (сеть рвёт большие колёса).
  **MinIO:** образ `minio/minio` удалён из Docker Hub (2025) — compose
  `MINIO_IMAGE` (по умолчанию `bitnamilegacy/minio:latest`); qdrant:latest
  скачан.
- **Проверки:** сьют 618 passed (+4: heartbeat ×2, doctor-лок ×2), vitest
  88/88, tsc чист, ruff чист. `docker compose config` валиден.
- **Запуск и живые правки (стек поднят, все сервисы healthy):** translate —
  transformers v5 убрал pipeline «translation» → пин `transformers>=4.44,<5`;
  rag — `migrations/env.py` импортировал несуществующий top-level
  `infrastructure` → `rag_core.infrastructure.repositories`; в рантайм-стейдж
  докерфайла добавлены COPY rag_core/rag_worker/rag_api (editable-установка
  ссылается на /app). rag_worker/rag_api/translate healthcheck —
  `start_period: 300s` (первая загрузка весов e5/NLLB с HF занимает минуты,
  иначе контейнер помечается unhealthy). miniapp — `wget http://127.0.0.1/`
  (localhost резолвится в ::1, nginx слушает только IPv4). MinIO: quay-зеркало
  официального образа закрыто (401) — остались на bitnamilegacy, named-volume
  `auto_bot_minio_data` разово chown на 1001:1001 (bitnami run-as non-root).
  `.env`: `RAG_LLM_PROVIDER=glm-5-turbo` → `glm` (pydantic-валидация провайдера;
  модель осталась `glm-5-turbo`). `ListingOut` + `source_language` (колонка
  была в ORM, забыли в схеме — фронт ждёт её для показа языка тайтла).
  Итог: 14/14 контейнеров healthy, one-shot миграции exit 0; смоук
  `POST /translate` → 200 с переводом; openapi отдаёт updated_at/
  last_seen_at/price_changed_at/source_language. Полный pytest 618 passed.
  HF-токен (опционально): без HF_TOKEN загрузка моделей медленная и
  rate-limited — можно добавить `HF_TOKEN` в .env и пробросить в
  translate/rag_worker.
- **Инцидент «вебапп не открывается»:** широкий `docker compose up -d` без
  `--profile` поднял gateway (micro), а у него сетевой алиас `backend` — DNS
  сети стал отдавать два IP на имя `backend` round-robin, и половина запросов
  nginx→API уходила в шлюз без каталога (`service 'catalog' unavailable` →
  502). Правило из №60 подтверждено: строго один профиль; gateway погашен
  (`docker compose stop gateway`), `/api/listings` стабильно 200.

## 64. Квота AI «за выдачу результата» вместо «за сообщение» (2026-10-17)

- **Фидбек юзера:** «сейчас нормируется 1 сообщение как 1 AI-поиск — на демо
  человек не может пообщаться; мерить надо не сообщением, а выдачей результата».
- **Метрика изменена:** `ai_daily_limit` (free 3 / pro 200 / business ∞) теперь
  списывается только когда агент **реально выдал результат**: preview_search
  показал варианты или finalize_search запустил полный поиск. Уточняющие
  вопросы, правки критериев, пустые выдачи — бесплатны. Реализация:
  `app/services/ai_quota.py` (Redis-счётчик `ai_quota:{uid}:{YYYYMMDD}`,
  TTL сутки); `ToolContext.quota_limit` → `run_agent(quota_limit=...)`;
  исчерпанный лимит → вежливый отказ с апселлом Pro, но диалог продолжается.
- **Обвязка:** `/api/dialog/message` берёт тариф юзера из БД, `/api/ai/message`
  — из JWT-юзера; analytics-событие `ai_search` (kind=preview/finalize, relaxed,
  count) для воронки. Тексты тарифов: «AI-сообщений/день» → «AI-поисков/день»
  (payments, бот-подписка, Profile.tsx), MENU_AI_HINT дополнен «диалог
  бесплатный».
- **Тесты:** +3 (сообщения не списывают, списание за preview-выдачу, блокировка
  без списания; finalize списывает; None = безлимит без записи). Сьют 614
  passed, vitest 88/88, tsc чист; контейнеры пересобраны, gateway погашен.
  Живая проверка на Redis: consume/allow/reset работают.

## 63. AI-подбор: контекст диалога + цвета + «мягкий» поиск + состояния ленты (2026-10-17)

- **Фидбек юзера:** «цвета не находит; ИИ-подбор жёстко завязан — "найди бричку
  до 30к для семьи чёрную" → пусто, "плохо ищешь" → опять та же заготовка;
  инфо-окна ломают вёрстку; пустое/ошибку в ленте рендерит карточкой без
  центрирования».
- **Цвета (фикс):** фильтр `color` вообще отсутствовал в `build_listing_query` —
  добавлен (ilike по канону). `normalize_color` подключён к `apply_patch`
  («чёрный»→black), справочник цветов в `FIELD_OPTIONS` + enum в tool-схемах —
  LLM выбирает из списка, а не выдумывает. В БД цвет пока только у свежих лотов
  (detail-enrichment) — старые добьёт backfill из MinIO (бэклог).
- **Контекст диалога:** ранний выход «Всё главное собрал» срабатывал на ЛЮБОЕ
  сообщение при заполненных критериях — теперь только на короткие подтверждения
  (`_is_confirmation`: «всё», «давай», «ок»…). Содержательные реплики («плохо
  ищешь», «подешевле») уходят в LLM. System-промпт дополнен блоком «ДИАЛОГ —
  ЭТО ОДИН НЕПРЕРЫВНЫЙ ПОИСК»: короткие реплики = правка текущих критериев +
  повторный preview_search, жаргон («бричка/тачка», «для семьи» → внедорожник/
  минивэн), критика = сигнал ослабить критерии.
- **«Мягкий» поиск:** `search_with_relaxation` в `search.py` — при пустом точном
  запросе progressive-relax: цвет → пробег → кузов/топливо/КПП/привод → годы ±3 →
  бюджет +20%; возвращает близкие варианты с пометкой «ослабил: …» (критерии
  пользователя не меняются). Совсем пусто → предложение ослабить параметры или
  запустить полный поиск (finalize), вместо голой заготовки.
- **Лента (вёрстка):** EmptyState/ErrorState вынесены из `.feed-grid` (на десктопе
  грид сжимал их в узкую карточку одной колонки). `.state` — full-width, центри-
  рование, max-width 480px, `grid-column: 1/-1` страховка для любых грид.
- **Тесты:** +7 (цвет-нормализация, enum в схеме, `_is_confirmation`, критика →
  LLM, relaxation на fake-сессии, пусто → предложение ослабить). Сьют 611
  passed, vitest 88/88, tsc чист; контейнеры backend/bot/miniapp пересобраны,
  gateway погашен. Живая проверка: relaxation на реальной БД отрабатывает
  (strict пусто → «цвет», «бюджет +20%»).

## 62. Спеки в listings + ИИ без выдумок + карточки/лента + сохранение диалогов (2026-10-17)

- **Фидбек юзера:** «нет данных о типе кузова вообще; непонятно как работает ИИ-подбор
  и есть ли данные в сессии; должен работать как MCP — брать инструментарий, а не
  выдумывать; вместо списка — пара карточек, если их мало, если много — переход в
  ленту с сохранённым фильтром; сессии должны сохраняться (посмотреть диалог спустя
  время), диалоги сносить через сутки с разговора».
- **Спеки в listings (этап 5):** миграция `0016_listing_specs_dialog_ttl.py` —
  listings.body_type/fuel/transmission/drive + dialog_sessions.updated_at. Адаптеры
  уже отдавали спеки в UnifiedListing, но `_to_row`/UPSERT их выбрасывали — потому
  «типа кузова нет вообще». Теперь: нормализация в `_to_row` (нераспознанное — как
  есть), UPSERT с `COALESCE(EXCLUDED.x, listings.x)` (не затирать), фильтры по спекам
  в `search.py` и `/api/listings` (ilike), спеки в ListingOut. История заполнится
  при следующих парсингах (raw лежит в MinIO, backfill позже).
- **ИИ как MCP, без выдумок:** справочники `FIELD_OPTIONS` в `app/ai/tools.py`
  (body_type/fuel/transmission/drive/seller_type — значения совпадают с ключами
  normalize_*); enum подставляются прямо в tool-схемы (update_criteria/preview_search/
  finalize_search) — LLM видит допустимые значения; `apply_patch` нормализует значения
  («седан»→sedan, «бензин»→petrol); опции `ask_clarification` для справочных полей
  заменяются справочными (лог `clarification_options_replaced`). preview_search и так
  показывает только реальные объявления из БД.
- **Формат выдачи (finalize):** count через новый `search_listings_page` (items+total):
  0 → «фильтр сохранён, пришлю уведомления»; ≤5 → фото-карточки в чат (с формами
  «вариант/варианта/вариантов»); >5 → кнопка «📱 Смотреть все (N)» — deep-link
  `/feed?make=...&year_from=...&price_max=...`. Feed принимает URL-параметры
  (`deepLinkQuery()`, цены в $, URL чистится replaceState). Параметры прокидываются
  и по спекам (body_type/fuel/transmission/drive).
- **Сессии диалогов:** архив в PG на **каждом** ходу (`dialog_message` →
  archive_session), не только при финализации; `updated_at` — время последнего
  сообщения. `POST /api/dialog/restore` + `restore_dialog()` — поднять последний
  диалог из архива в Redis (новый session_id, критерии+история переносятся; активный
  не трогаем). Бот: кнопка «🕘 Продолжить прошлый диалог» в меню AI + callback
  `dlg:restore` (показывает историю `_format_history`, дальше обычная панель).
  Чистка: cron `dialog_cleanup_job` (04:00 UTC, workers/send.py) сносит архивы
  старше `dialog_ttl_sec` (24ч) от updated_at; Redis-state и так TTL 24ч.
- **589 pytest (+10: нормализация патчей, enum в схеме, справочные опции
  ask_clarification, restore×3, cleanup, UPSERT-спеки; CRON_JOBS-тест обновлён),
  85+ vitest, ruff/tsc чисто.**
- **Живая проверка спеков (прод):** разовый fetch kcar (1 стр.) → 100 объявлений
  с body_type. Корейские категории carctgrNm (중형차/경차/승합차/화물차…)
  замаплены в канон во втором коммите (`fix(normalize)`): итого suv 46, sedan 41,
  hatchback 8, pickup 4, van 1. Encar list-API BodyType/transmission/drive не
  отдаёт (fuel отдаёт) — обогащение из detail-страниц в бэклог. Дедуп-кэш
  (dedup:*, TTL 48ч) при разовом рефетче чистится: `redis-cli --scan --pattern
  "dedup:*" | xargs -r redis-cli del`. Грабля-продолжение: `docker compose up`
  после упавшего recreate оставляет контейнеры-сироты вида
  `1b11f34f9fae_auto_bot-backend-1` — снимать `docker rm -f` и поднимать поимённо.

# Auto Selection — PROGRESS

## 60. Фикс входа через Telegram (диагностика + окно забора JWT)

- **Симптомы:** (а) в миниаппе «вход через ТГ» → бот отвечает «✅ подтверждён», но аппка снова показывает форму; (б) на сайте после возврата из бота — «⌛ Ссылка входа устарела».
- **Диагностика:** цепочка код→бот→JWT проверена curl'ом end-to-end на проде (200 на `/telegram/link` → `/link/confirm` → `/link/{code}` — JWT отдаётся). Бот коды подтверждал (логи, 200). Найдено в логах: `POST /api/auth/telegram` (initData-логин миниаппа) — **стабильно 401**; причина 401 в лог не писалась.
- **Фиксы:**
  - `app/api/auth.py`: `logger.warning("initData login rejected: %s", exc)` в `_authenticate_via_init_data` — точная причина (hash mismatch / expired / user missing) теперь видна в логах.
  - `_LOGIN_CONFIRM_TTL_SEC` 120 → **600** (= TTL кода): юзер может сидеть в боте дольше 2 минут.
  - `frontend/src/screens/Login.tsx`: `TG_POLL_MAX_TRIES` 72 → **240** (~10 мин, синхронно с бэком).
- **Деплой:** прод `backend`+`miniapp` пересобраны и подняты поимённо (`docker compose -p auto_bot --profile mono build/up --no-deps`; бот/воркер не тронуты), тест-стенд `-p auto_bot_test` с оверрайдом аналогично. Проверено в контейнере: TTL600 + warn-лог на месте; 8443 и `/auth/telegram/link` живые.
- **Тесты:** `test_auth_api.py` 9 passed в новом образе; vitest `Login.test.tsx` 3 passed.
- **⏳ Открытый вопрос:** почему initData-логин 401. `BOT_TOKEN` подтверждён через getMe (`@ftadasd_bot`/7048715401 — тот же бот, что подписывает initData), алгоритм `validate_init_data` покрыт тестами. Следующий шаг: повторить вход в аппке и прочитать `docker logs auto_bot-backend-1 | grep 'initData login rejected'`.

## 61. КОРЕНЬ ВХОДА НАЙДЕН: Telegram Desktop подписывает initData по раскодированным значениям

- **Диагноз (доказано вычислением):** временно залогировали полный raw initData юзера в проде и пересчитали HMAC в контейнере с токеном из `.env`. `hash` юзера НЕ совпал с канонической схемой (подпись по URL-encoded значениям), но **ТОЧНО совпал** (`bb5c8623...`) при подписи по **раскодированным** (`unquote`) значениям — Telegram Desktop (2026) считает data_check_string по декодированным данным, вопреки доке («data_check_string is built exactly as received, with url-encoded values»).
- **Фикс `app/core/security.py::validate_init_data`:** каноническая проверка → при `hash mismatch` fallback на dcs из `unquote`-значений. Оба варианта покрыты тестами.
- **Тесты `tests/test_security_initdata.py` (новый, 4):** канон-подпись ок; **desktop decoded-подпись ок**; битая подпись → `hash mismatch`; старый `auth_date` → `expired`. Полный suite: **579 passed** (575+4).
- **Проверка на реальных данных:** прод-контейнер принял живой initData юзера (`ACCEPTED: 809464979 PashtetMedved`) до всякого клика — авто-вход в миниаппе теперь работает.
- **Деплой:** прод backend (build+up --no-deps) и тест-стенд `-p auto_bot_test` с оверрайдом. Временный raw-лог убран, оставлен компактный `initData login rejected: <reason>`; скрипт разбора — `shots/analyze_initdata.py`.
- **Грабли (запомнить):** 401 при валидном токене — не спешить менять токен; посчитать HMAC по обоим вариантам dcs (encoded/decoded) на реальном initData из лога. В изолированном прогоне pytest в контейнере копировать `pyproject.toml` — иначе `asyncio_mode` не применится и все async-тесты падают (ложные «152 failed»).

## 32. raw_payload → S3/MinIO (фаза 6)

- **boto3 vs aioboto3 решено: aioboto3** — fetch/send воркеры асинхронные,
  синхронный boto3 блокировал бы event loop. **Миграция `0012_raw_payload_s3`**:
  колонка `listings.raw_payload` (JSONB, раздувала БД/бэкапы) выпилена, вместо
  неё `raw_key` (TEXT) — ключ S3-объекта; NULL = не загружен (MinIO лежал /
  флаг выключен).
- **`S3Storage`** (`app/services/storage.py`): ленивый импорт aioboto3,
  botocore `Config` (retries=3, connect 5s / read 30s), MinIO через
  `endpoint_url`; ключ `raw/{source}/{yyyy}/{mm}/{listing_id}_{content_hash}.json`
  (retention по префиксу + бесплатная дедупликация: хэш не менялся — объект
  не перезаписываем); S3 Metadata (source/listing_id/content_hash/fetched_at);
  `put_raw`/`presign_raw`/`delete_older_than`/`ensure_bucket` — все с
  fallback: ошибка → warning в лог, объявления сохраняются.
- **`save_listings(..., storage=)`**: NEW/смена `content_hash` → параллельная
  загрузка (`asyncio.gather`, `return_exceptions`) + executemany
  `UPDATE listings SET raw_key`; без изменений → загрузки нет.
  Fetch-воркер создаёт storage в startup-ctx (`ensure_bucket`).
- **Retention-крон** `raw_retention_job` 03:30 UTC: list_objects_v2 пагинацией
  → пакетный delete_objects (≤1000), объекты старше `RAW_RETENTION_DAYS=180`.
- **Админ-endpoint `GET /api/admin/listings/{id}/raw`** (internal token):
  presigned GET на 10 мин (`RAW_PRESIGN_TTL_SEC`), прямого доступа к MinIO
  у админки нет; raw_key NULL → 404, storage недоступен → 502.
- Конфиг: `RAW_PAYLOAD_ENABLED` (по умолчанию false), `S3_ENDPOINT_URL`/
  `S3_BUCKET`/`S3_ACCESS_KEY`/`S3_SECRET_KEY`/`S3_REGION` (через `env_file: .env`
  в compose; в docker-сети endpoint `http://minio:9000`). PLAN: открытый
  вопрос закрыт; фаза 7 — добавлен план (RANGE по `recorded_at`, PK обязан
  включать ключ, pg_partman, связка «горячее/холодное» с MinIO).
- **Edge-кейсы (rev. 2)**: retention-крон возвращает ключи удалённых объектов
  и обнуляет `listings.raw_key` (`UPDATE ... WHERE raw_key = ANY(:keys)`,
  чанки по 1000) — иначе через 180 дней админка генерит presigned URL в
  никуда. `ensure_bucket` различает `AccessDenied` (токен без CreateBucket →
  warning «создайте вручную», не stack trace). Гонка retention × fetch
  проанализирована и исключена: raw_key ставится только для NEW/смены хэша,
  ключ содержит yyyy/mm парсинга — совпасть с удаляемым (≥180 дней) не может;
  обнуление только по точному совпадению ключа. `downgrade()` 0012 возвращает
  только схему, данные — нет (зафиксировано в миграции).
- Тесты: **314 passed** (+14: формат ключа, загрузка NEW/raw_key-UPDATE,
  unchanged без загрузки, fallback при ошибке S3, disabled, retention
  (удаление + обнуление raw_key, no-op без БД), ensure_bucket
  (создание / AccessDenied), admin presigned URL / 404), ruff чист,
  `alembic heads` → `0012`, `docker compose config` ОК.
- **⏳ Статус: код готов, e2e с живым MinIO НЕ проверен** (главный техдолг
  фазы): реальный fetch → объект по ключу → presigned URL открывается →
  повторный fetch не перезаписывает → retention с RAW_RETENTION_DAYS=0/1
  удаляет и обнуляет raw_key → сценарий с токеном без CreateBucket.
  Проверить на копии БД: `alembic downgrade -1` → `upgrade head`.

## 33. Подготовка `listing_price_history` к партиционированию (фаза 7, без включения)

- Партиционирование в PG не выключается флагом: `PARTITION BY RANGE` — сразу
  партиционированная таблица. Поэтому схема готовится заранее (вариант «B»),
  включение — строго по триггеру (1–2 млн строк).
- **Миграция `0013_price_history_partition_ready`**: PK `(id)` →
  `(id, recorded_at)` (ключ RANGE обязан входить в PK; `id` оставлен как
  tiebreaker `ORDER BY recorded_at, id`) + `recorded_at` NOT NULL. Данные не
  переносятся, таблица не партиционируется. Индекс
  `idx_price_history_listing (listing_id, recorded_at)` уже содержал ключ.
- **Аудит запросов** (PLAN §6): favorites first_price / listings график —
  полная история по `listing_id` (границы по `recorded_at` не добавлены —
  сломали бы семантику; при триггере — опциональный `since`), send
  `_old_price` — `LIMIT 2` по индексу. Все запросы — через
  `idx(listing_id, recorded_at)` → per-partition index scans.
- ORM: `ListingPriceHistory.recorded_at` — `primary_key=True, nullable=False`
  (композит с `id`); вставка идёт raw SQL — поведение не меняется.
- Тесты: 314 passed, `alembic heads` → `0013`.

## 31. Перевод тайтлов: NLLB-микросервис (фаза 5)

- **Размещение NLLB (открытый вопрос PLAN) решено**: отдельный optional-сервис
  `services/translate` (FastAPI + `nllb-200-distilled-600M`, CPU-образ, порт
  8200), не в RAG: изоляция отказа (перевод некритичен) и памяти
  (~2 ГБ RSS), независимый деплой/своп модели. Bearer `API_TOKEN` (опционально),
  `GET /health`, warmup при старте, детект языка по письменности
  (`kor_Hang`/`zho_Hans`/`rus_Cyrl`/`eng_Latn`) для `source_lang=auto`,
  in-process кэш, батчи через transformers pipeline.
- **Миграция `0011_translations`**: `listings.title_raw` (оригинал — до фазы 5
  title вообще не персистился, только content_hash), `title_ru`,
  `source_language`, `translated_at`. Upsert пишет `title_raw`.
- **Триггер перевода — тот же `content_hash`**: `save_listings` возвращает
  `translate_ids` (NEW + смена хэша) → fetch-воркер ставит задачу
  `translate_listings` (та же очередь `fetch`), которая батчами
  (TRANSLATE_BATCH_SIZE) зовёт сервис и пишет `title_ru/source_language/
  translated_at` (executemany). Ошибка сервиса → title_ru остаётся NULL,
  UI показывает оригинал.
- **`TranslateClient`** (`app/services/translate_client.py`): батчевый
  POST /translate, Redis-кэш `tr:{tgt}:{src}:{sha1}` TTL 30 дней,
  retry с backoff (5xx не ретраим), circuit breaker (3 ошибки → пауза 5 мин +
  алерт), `TRANSLATE_ENABLED` + `TRANSLATE_SERVICE_URL` (пусто = выключен),
  `TRANSLATE_SOURCE_LANGS` (JSON: encar/kcar → kor_Hang, che168 → zho_Hans).
- **UI**: `ListingOut` + `title_raw`/`title_ru`; Mini App — подзаголовок
  с переводом на карточке, «Название» на экране объявления; карточка бота
  предпочитает `title_ru`.
- Тесты: **300 passed** (+15: клиент — кэш/batch/fallback/CB, задача
  `translate_listings`, `translate_ids`, карточка с title_ru), ruff чист,
  `alembic heads` → `0011`, `tsc`/`vite build` ОК.

## 30. Отслеживание объявлений: наличие, история цен, статусные уведомления (фазы 1-4)

- **Миграция `0009_listing_tracking`**: `content_hash`, `is_active`,
  `first_seen_at`/`last_seen_at` (бэкфилл из `parsed_at`), `disappeared_at`/
  `price_changed_at`, таблица `listing_price_history`, partial-индекс активных,
  индекс по `last_seen_at`, BRIN по `first_seen_at`, индекс истории цен.
- **Upsert переписан на CTE** (1 запрос): `old_price`/`old_currency`/`old_hash`/
  `was_active` из `existing` (FOR UPDATE) + RETURNING; `first_seen_at` не
  сбрасывается, `disappeared_at = NULL` при реактивации; дедуп: cross-source
  Redis до записи, in-source — `ON CONFLICT` (source, source_id).
- **`compute_content_hash`** = sha256(title|price|currency|mileage|sorted images);
  смена хэша — единый триггер «объявление изменилось» (в фазе 5 — повторный
  перевод тайтла). Точки цены пишутся при NEW и смене пары (price, currency).
- **Фаза 3**: `0010_sent_log_kind` (PK → user_id+listing_id+kind), `mark_stale`
  (CB-aware, RETURNING id), cron `stale_check_job` 04:00 UTC, статусные
  уведомления `listing_status_scan` → `send_listing_status`
  (price_drop/gone/returned; аудитория — получатели из sent_log; rate limit,
  тихие часы, антидубль по kind).
- **Фаза 4**: история цен в UI — `PricePointOut`, в `ListingOut` добавлены
  `is_active`/`first_seen_at`/`first_price`/`price_history`; карточка
  `GET /api/listings/{id}` отдаёт `price_history` (ASC; в ленте — нет, иначе N+1),
  `GET /api/favorites` — `first_price` одним батч-запросом. Mini App: секция
  «История цен» на экране объявления, бейджи «⬇️ −N%» / «🔴 Исчезло» на карточке
  и «⬇️ (было X)» / «🔴» в избранном.
- Тесты: **285 passed** (+1 к 284), ruff чист, `alembic heads` → `0010`.

## 29. OpenAI выпилен + ParserDoctor: триаж причин падения источника

- **LLM только за Китай**: провайдер OpenAI удалён из фабрики
  (`glm | anthropic | ollama | mock`), `OpenAICompatibleClient` →
  `ChatCompletionsClient` (GLM/DeepSeek/Ollama), `openai_api_key` из
  config/.env/.env.example убран, модели `gpt-4o*` → `glm-4.6`,
  неизвестный провайдер → явный `LLMError`. Обновлены README/PROGRESS/тесты.
- **ParserDoctor — триаж** (`classify_page` / `classify_fetch_error`):
  - `banned` — HTTP 401/403/406/429 или 2xx-страница с маркерами антибота
    (`captcha`, `安全验证`, `滑动验证`, `punish`, `waf`, …) или пустышка
    < 300 символов → алерт «🚫 бан/геоблок — ротация прокси», LLM не зовём;
  - `server_down` — 5xx, таймауты, ошибки коннекта (HttpClient заворачивает
    их в `FetchError`, код парсится regex) → алерт «🛑 источник недоступен»;
  - `http_error` — прочие 4xx (404/410: страница списка переехала) →
    подсказка проверить `base_url/list_path`;
  - `markup_change` — страница живая, карточек 0: только тут зовём GLM.
- **Cooldown переставлен**: ключ `doctor:{source}` ставится только перед
  LLM-ремонтом — бан и падение сервера триажатся при каждом запуске врача
  (раньше cooldown 6 ч глотал бы повторные диагнозы).
- Тесты: **264 passed** (+5: 403/5xx/капча-200/c cooldown/unit-классификаторы);
  ruff чист.


## 28. Фаза 5 — deferred [бэкенд] items (UI/UX roadmap wrap-up)

- **Batch-claim**: `POST /api/partner/leads/batch` `{"limit":1..10}` — атомарно
  забирает до N доступных лидов с учётом дневной квоты (исчерпание → 429,
  как у одиночного claim). Сервис `partners.claim_batch`, аналитика
  `lead_sold` c `batch: true`, кнопка «⚡ Взять 3» в пуле ЛК.
- **Биллинг**: каталог тарифов `PLAN_CATALOG` (starter 5/$49, pro 15/$149,
  salon 40/$399), `GET /api/partner/billing` (план, квота, расход), экран
  «Биллинг» в ЛК вместо заглушки (прогресс-бар квоты + таблица тарифов).
- **Тихие часы**: применялись в send worker (фильтр → `NOTIFY_QUIET_HOURS_DEFAULT`);
  добавлены настройка из бота (меню «Уведомления → 🌙 Тихие часы», пресеты
  23:00–08:00 / 22:00–10:00 / выкл через `BackendClient.quiet_hours`) и
  поддержка в `POST /api/notifications/settings` (notify_mode опционален,
  `quiet_hours_clear` для сброса).
- **AI-помощник админа**: `app/services/admin_assistant.py` — tool-loop
  (до 3 итераций) поверх `build_llm_client` (GLM, `LLM_PROVIDER=glm`);
  tools: `leads_stats`, `partners_list`, `sources_health` (только чтение);
  `POST /api/admin/assistant/chat` (X-Internal-Token; LLM недоступен → 503);
  `ChatWidget.tsx` подключён к эндпоинту (история, 503-подсказка про ключ).
- Тесты: **259 passed** (+15: batch-claim сервис/API, биллинг, ассистент);
  ruff чист; фронт `tsc + vite build` — ок.
- Напоминание владельцу: `alembic upgrade head` (0004–0008) и вписать
  `GLM_API_KEY` в `.env` (иначе ассистент отвечает 503 с подсказкой).

> Экосистема подбора автомобилей из-за рубежа (Корея, Китай, ОАЭ и др.) для
> пользователей из РБ / РФ / СНГ: Telegram-бот + WebApp (Mini App) + сайт
> с AI-диалогом, парсингом источников, RAG-базой знаний и монетизацией.
> Реализация ТЗ (`TZ.md`). Обновлено: 11.10.2026 · **208 тестов backend** ·
> дерево чистое.

**Легенда статусов:**

| Метка | Значение |
|---|---|
| ✅ | Готово, покрыто тестами, в кодовой базе |
| ⏳ | Код готов — **ждёт действия владельца** (миграция, creds, живая проверка) |
| 📋 | Бэклог, не начато |

---

## 0. Статус-дашборд

| # | Блок | Статус | Что осталось |
|---|---|---|---|
| 1 | Этапы ТЗ 0–7: бот, backend, AI-агент, RAG, Mini App | ✅ | — |
| 2 | Этап 8: мониторинг, алерты, `/health/detailed`, `/metrics` | ✅ | — |
| 3 | Планировщик: `schedule_cron` + интервальные задачи | ✅ | — |
| 4 | Продуктовая аналитика (P1-7): `analytics_events`, хуки | ✅ | — |
| 5 | Лиды: `POST /api/leads`, `PATCH /api/leads/{id}` | ✅ | — |
| 6 | Адаптер Che168 (P2-10) | ⏳ | `alembic upgrade head`, сверка живого HTML/гео, `enabled=true` |
| 7 | ParserDoctor (ИИ-ремонтник селекторов) | ⏳ | код готов, `doctor_mode=off`; включается в БД |
| 8 | Прокси-шлюз DataImpulse (антибан) | ✅ | включается переменными `.env` при банах |
| 9 | Миграции `0004`–`0006` (аналитика, che168, партнёры) | ⏳ | `alembic upgrade head` |
| 10 | Telegram Stars монетизация | ⏳ | код-путь полный; живой тестовый платёж |
| 11 | Живой E2E RAG-стека (GLM + ingest + запрос) | ⏳ | creds + `ingest_kb.py --force` |
| 12 | Token Bucket Telegram ~30 msg/s (P2-9) | 📋 | кандидат №1 следующего спринта |
| 13 | SQL-дашборды воронок (retention D1/D7, конверсии) | 📋 | кандидат №2 |
| 14 | LLM cost tracking (P3) | 📋 | — |
| 15 | Ротация PQ-ключей (P3) | 📋 | — |
| 16 | Push `refactor/monorepo` (rag_service) | 📋 | ветка готова локально |
| 17 | HTTPS `MINIAPP_URL` для прода | 📋 | домен/TLS |
| 18 | Рефакторинг веб-авторизации (nullable telegram_id) | 📋 | порог ~30k веб-юзеров |
| 19 | Партнёрский контур (P2-11): API ключей, claim лидов, CPL | ✅ | накатить `0006`, создать первого партнёра, веб-ЛК |

### Действия владельца (единственное, что мешает «зелёному» статусу)

- [ ] `uv run alembic upgrade head` — накатить `0004` + `0005` (пп. 6, 9).
- [ ] Che168: сверить селекторы с живым HTML и гео-доступность (403 из РБ →
      включить DataImpulse с `PROXY_DATAIMPULSE_GEO=cn`), затем
      `UPDATE sources SET enabled=true WHERE name='che168'` (п. 6).
- [ ] Опционально: `"doctor_mode": "suggest"` в `sources.config` che168 (п. 7).
- [ ] Партнёрский контур: после миграции создать партнёра
  `POST /api/admin/partners` — ключ показывается один раз;
  договориться с 2–3 салонами/брокерами о пилоте (п. 19).
- [ ] Telegram Stars: тестовый платёж XTR (п. 10).
- [ ] RAG E2E: `GLM_API_KEY`, PQ-ключи, `ingest_kb.py --force`, контрольный
      запрос к `rag-api` (п. 11).

---

# Часть I — обзор проекта

## 1. Что это и для кого

Пользователь заходит в Telegram-бота или Mini App и на естественном языке
описывает, какой автомобиль хочет (марка/модель, бюджет, год, город).
AI-агент собирает критерии, ищет варианты по агрегаторам (Encar, KCar,
Che168), показывает карточки, отвечает на вопросы о доставке/растаможке через
RAG по базе знаний, считает экономику «под ключ» в USD и подписывает
пользователя на уведомления о новых вариантах. Монетизация — Pro-подписка
через Telegram Stars; лиды из диалога отдаются менеджерам.

## 2. Технологический стек

| Слой | Технологии |
|---|---|
| Backend | Python 3.13, FastAPI, uvicorn, Pydantic v2 / pydantic-settings |
| Бот | aiogram 3 (FSM в Redis), long polling |
| БД | PostgreSQL + SQLAlchemy 2 (async) + Alembic (0001–0005) |
| Очереди | RabbitMQ (aio-pika), producer/TaskWorker/DLQ/cron |
| Кэш/состояние | Redis (FSM, DialogState, кэши, прокси-пул, антидубли, cooldown'ы) |
| Парсеры | httpx + selectolax/lexbor (SSR), общий HttpClient (ретраи, 429, прокси) |
| Frontend | React 18 + Vite + TypeScript, @telegram-apps/telegram-ui, React Router |
| RAG-сервис | FastAPI + RabbitMQ worker, Qdrant (гибрид vector+BM25/RRF), MinIO, multilingual-e5 |
| LLM | GLM (Zhipu, основной), fallback: DeepSeek / Anthropic / Ollama / mock (OpenAI выпилен) |
| Криптография | PyJWT, PBKDF2-SHA256 (пароли сайта), гибрид ML-KEM-768 + X25519 + AES-256-GCM (документы) |
| Инфра | Docker Compose (postgres, redis, rabbitmq, qdrant, minio, backend, bot, workers, miniapp, rag_*) |
| Тесты | pytest + pytest-asyncio (**208 тестов** backend), контрактные тесты адаптеров, mypy, ruff |

## 3. Архитектура

```
Пользователь ── Telegram ──► Бот (aiogram3) ─┐
Пользователь ── WebApp/сайт ─► Backend (FastAPI) ── PostgreSQL
                                  │  ▲
                    AI-агент ─────┤  └─ RAG API (:8100) ─► RabbitMQ ─► RAG Worker (:8101)
                    (tool loop)   │                          │
                                  ▼                          ├─ Qdrant (гибридный поиск)
                  Scheduler (schedule_cron) ─► Fetch Worker ─► адаптеры ─► listings
                                                     │  ▲
                                                     │  └─ ParserDoctor (GLM чинит селекторы)
                                                     └─ ProxyPool / DataImpulse (антибан)
                                  ▲
                          Send Worker ─► уведомления в Telegram
```

Потоки:
- **Диалог**: пользователь → агент (LLM + tools) → поиск/вопрос → ответ;
  заинтересованность → `POST /api/leads` → менеджер.
- **Парсинг**: cron/ручной запуск → очередь fetch → адаптеры
  (Encar API / KCar API / Che168 HTML) → нормализация → кросс-дедуп →
  `listings`; при смене вёрстки — ParserDoctor, при банах — прокси-шлюз.
- **Уведомления**: новые листинги → матчинг активных фильтров → Send Worker →
  instant или digest (тихие часы, антидубли).
- **RAG**: `POST /api/v1/ask` (rag_api) → JWT → RPC `query_queue` → worker →
  гибридный поиск Qdrant + LLM-генерация → ответ с источниками и confidence.
- **Аналитика**: хуки в API/боте → `analytics_events` → SQL-агрегаты,
  `events_24h` в `/metrics`.

## 4. Структура репозитория

```
auto_bot/
├── app/                      # Backend
│   ├── api/                  # HTTP-роутеры (см. §6)
│   ├── ai/                   # агент, LLM-клиенты, RAG-клиент, промпты, tools
│   ├── adapters/             # encar, kcar, che168, registry, прокси, дедуп
│   ├── bot/                  # aiogram: handlers, button_flow, __main__
│   ├── core/                 # config, security (initData/JWT), deps, pq_crypto
│   ├── db/                   # models, session, migrations 0001–0005 (alembic)
│   ├── queue/                # RabbitMQ: producer, TaskWorker, DLQ, cron
│   ├── schemas/              # Pydantic-контракты (UnifiedListing, Lead…)
│   ├── services/             # users, dialog, search, notify, subscription,
│   │                         # fetch, analytics, alerting, source_health,
│   │                         # cron, parser_doctor
│   ├── workers/              # fetch, send
│   └── main.py               # FastAPI app (uvicorn app.main:app)
├── frontend/                 # Mini App + сайт (React/Vite/TS)
├── services/rag/             # RAG-микросервис (rag_core/rag_worker/rag_api + kb/)
├── tests/                    # pytest (208 тестов)
├── docker-compose.yml        # весь стек
├── pyproject.toml            # uv-проект backend
├── TZ.md                     # техническое задание
├── PROGRESS.md               # этот файл
└── .env.example              # шаблон конфигурации
```

## 5. Модели данных (основные)

- **User** — telegram_id (PK), username, tier (free/pro), notification_mode,
  **email (unique)** + password_hash (сайт), синтетические отрицательные
  telegram_id для веб-пользователей (PK/FK сквозной).
- **Filter** — SearchCriteria пользователя (марка/модель/цена/год/источники/город).
- **Listing (UnifiedListing)** — нормализованное объявление: VIN (у Che168
  скрыт → None), источник, цена (валюта источника), год/пробег/объём, страна.
- **Favorite** — избранное (миграция `0002_favorites`).
- **Subscription/Payment** — Pro через Telegram Stars.
- **DialogSession** — архивация DialogState из Redis.
- **Lead** — обращение из диалога: user_id, listing_id?, статус
  new/in_progress/closed/rejected, manager_id (таблица в `0001`).
- **AnalyticsEvent** — сырые события воронки (`0004_analytics_events`).
- **Source (админ)** — реестр источников: adapter_class, kind (api/parser),
  приоритеты, rate limits, `schedule_cron`, capabilities, `enabled`,
  `credentials_ref`; миграция `0005` добавляет `che168` (enabled=false).
- Миграции: `0001_initial`, `0002_favorites`, `0003_web_auth` (email unique),
  `0004_analytics_events`, `0005_add_che168_source`.

---

# Часть II — подсистемы

## 6. HTTP API (FastAPI, prefix `/api`)

| Роутер | Эндпоинты |
|---|---|
| `auth` | `POST /auth/telegram` (initData→JWT), `POST /auth/register`, `POST /auth/login`, `POST /auth/magic-link` |
| `users` | upsert профиля |
| `me` | `GET /me` — профиль + tier |
| `filters` | CRUD: `GET/POST /filters`, `PATCH/DELETE /filters/{id}` |
| `listings` | `GET /listings` (поиск/пагинация), `GET /listings/{id}` |
| `favorites` | `GET/POST/DELETE /favorites…` |
| `leads` | `POST /leads` (internal), `PATCH /leads/{id}` (internal: статус, manager) |
| `payments` | `POST /payments/stars` (Telegram Stars), `POST /payments/success` |
| `admin` | `GET/PUT /admin/sources…`, `POST /admin/sources/{name}/fetch`, health источников (X-Internal-Token) |
| `dialog` | `POST /dialog/message`, `POST /dialog/reset` (+ хуки аналитики) |
| `ai_search` | `POST /ai/message`, `POST /ai/reset`, `POST /ai/finalize` (JWT) |
| `notifications` | `POST /notifications/settings` (instant/digest/off), `POST /notifications/scan` |
| system | `GET /health`, `GET /health/detailed` (postgres/redis/rabbitmq, 503 при недоступной БД), `GET /metrics` (агрегаты + `events_24h`; **X-Internal-Token**) |

## 7. Telegram-бот ✅

- Команды (`set_my_commands`, личные чаты): `/start`, `/menu`, `/reset`,
  `/help`, `/stop`.
- **Кнопка-меню с WebApp**: «🚗 Поиск авто» открывает Mini App (`MINIAPP_URL`,
  HTTPS в проде).
- Inline-меню `/menu`: AI-подбор, «📱 Открыть приложение» (WebAppInfo),
  уведомления (мгновенно/дайджест/выкл), подписка, сброс диалога.
- FSM в Redis (`fsm:{user_id}`), кнопочный fallback (`button_flow.py`) при
  недоступности LLM.
- Уведомления: instant (rate-limit) или digest (3–5 карточек, тихие часы,
  антидубли `notified:{uid}:{lid}` TTL 7д).

## 8. AI-агент ✅

- LLM-клиенты (`app/ai/llm_client.py`): `OpenAICompatibleClient` (GLM —
  основной, DeepSeek, OpenAI, Ollama), `AnthropicClient`, `MockLLMClient`
  (тесты/dev), фабрика по `LLM_PROVIDER`.
- Tool loop с подтверждением фильтров; лимит шагов, ранний выход при
  заполненных критичных полях, история 8 сообщений; `DialogState` в Redis
  (`dialog:{user_id}`, TTL 24ч), архивация в `dialog_sessions`.
- Финализация = фильтр в БД (для уведомлений) + задачи парсинга по источникам.
- RAG (`app/ai/rag_client.py`): таймаут 5с, кэш Redis 1ч, circuit breaker
  (5 ошибок → пауза 5 мин), confidence ≥ 0.6 → ответ из БЗ, иначе fallback
  на LLM, затем честное «уточните у менеджера». Промпт-защита от инъекций.

## 9. RAG-микросервис (services/rag) ✅

- `rag_core/` — hexagonal-ядро: domain / application / infrastructure.
- `rag_worker/` — консьюмеры RabbitMQ: `query_queue`, `ingest_queue`,
  `delete_queue`, `reindex_queue`; health/metrics :8101; DLQ/retry.
- `rag_api/` — HTTP-шлюз `POST /api/v1/ask` (internal token → JWT → RPC), :8100.
- Поиск: гибрид vector (multilingual-e5) + BM25/RRF в Qdrant; embedding-кэши;
  опционально query rewriting, semantic cache, parent-child chunking.
- LLM-слой: мультипровайдерный роутер (GLM основной), кэш ответов,
  circuit breaker, лимит `max_tokens`, per-request routing с allowlist.
- Инфра: Qdrant, MinIO (документы), Postgres (реестр документов), Alembic.
- Заливка БЗ: `scripts/ingest_kb.py --force` (поддерживает PQ-шифрование).

## 10. База знаний (services/rag/kb) — РБ / РФ / СНГ + источники ✅

| Файл | Содержание |
|---|---|
| `customs.md` | Единые ставки пошлин ЕАЭС по возрастам (Решение ЕЭК № 107, прил. 6), коммерческий ввоз, РБ (утильсбор, ГАИ), КЗ/Армения/Кыргызстан, порядок действий |
| `fees.md` | Утильсбор РФ (ПП 1291), платежи РБ, СБКТС/ЭПТС, ОСАГО, налоги; **правила конверсии KRW/CNY/EUR/BYN/RUB → USD** (запас ±3–5%) |
| `countries.md` | Источники: Корея (аукционы, Ro-Ro Пусан→Владивосток), Китай (NEV, лицензированный экспортёр, Хоргос/Маньчжурия), ОАЭ, Европа/США, Япония; методика расчёта «под ключ» |
| `delivery.md` | Таблица маршрутов/сроков, что входит во фрахт, фотофиксация, трекинг |
| `risks.md` | Юридические, технические, финансовые риски; чек-лист сделки |
| `legal.md` | Правовая база РФ/РБ/ЕАЭС, экспортные ограничения стран-источников, compliance: обход санкций не поддерживается |
| `faq.md` | FAQ: регистрация, валюты, растаможка по возрастам, уведомления, документы |

Все ставки помечены «проверять на дату ввоза» — зафиксированы только
устойчивые нормы, летучие цифры — с явными оговорками.

## 11. Frontend (Mini App + сайт) ✅

Дополнительно (фазы 1–4 UI/UX ТЗ): полировка Mini App (фото, skeleton, тосты,
динамические чипсы, бесконечный скролл, галерея, онбординг 3 шага + миграция
0007 `users.onboarded_at`), партнёрский ЛК `/partner/*`, админка `/admin/*`
(источники/партнёры/лиды, вход по internal-токену), audit log (миграция 0008
`admin_actions`, логируются create/rotate партнёра), скелет AI-виджета в админке.

UI/UX-ТЗ для всех поверхностей: `docs/UIUX_TZ.md` (v1.0 — дизайн-токены,
Mini App, админка, партнёрский ЛК, бот, тон голоса, чеклист внедрения).

- Экраны: `Onboarding`, `Feed` (лента с чипсами-фильтрами), `Filters`,
  `Listing`, `Favorites`, `Profile`, `AiSearch`, `Login` (email+пароль).
- `api/`: `client.ts` (JWT, base `/api`), `auth.ts`, `ai.ts`, `types.ts`.
- Telegram-интеграция: `telegram-web-app.js`, initData → JWT, тема
  `--tg-theme-*`; в проде — HTTPS `MINIAPP_URL`.
- Вне Telegram — read-only лента + Login для полного доступа.

## 12. Безопасность ✅

- **Telegram initData**: HMAC-SHA256-валидация, max age 24ч.
- **JWT**: access 1ч + refresh 30д (`APP_SECRET_KEY`, HS256).
- **Пароли сайта**: PBKDF2-SHA256, 240k итераций; email — regex-валидация.
- **Веб-пользователи**: синтетические отрицательные telegram_id (PK/FK),
  ретрай 5× при коллизии (долг: отдельный PK, см. бэклог).
- **Внутренние эндпоинты**: `X-Internal-Token` (`INTERNAL_TOKEN`; пустое —
  обратная совместимость с `APP_SECRET_KEY`).
- **RAG**: internal token backend↔rag_api, JWT rag_api↔worker, ротация
  ключей HS/RS/ES, PII-редакция перед записью диалогов.
- **Постквантовое шифрование документов**: гибрид ML-KEM-768 (FIPS 203) +
  X25519 → HKDF-SHA256 → AES-256-GCM, контейнер `AUPQ1`, AAD = ключ объекта.
  `EncryptedFileStorage` над MinIO; включение `ENCRYPT_DOCUMENTS=true` +
  PQ-ключи. Устойчиво к «harvest now, decrypt later».

## 13. Конфигурация (.env)

Основные переменные (полный список — `.env.example`):
`APP_ENV`, `APP_SECRET_KEY`, `INTERNAL_TOKEN`, `APP_BASE_URL`, `MINIAPP_URL`,
`BOT_TOKEN`, `ADMIN_CHAT_ID`, `MANAGER_CHAT_ID`, `DATABASE_URL`, `REDIS_URL`,
`RABBITMQ_URL`; LLM: `LLM_PROVIDER=glm`, `GLM_API_KEY`, `GLM_BASE_URL`,
`LLM_DEFAULT_MODEL=glm-4.6`; RAG: `RAG_SERVICE_URL`, `RAG_API_TOKEN`,
`RAG_JWT_SECRET`, `RAG_CONFIDENCE_THRESHOLD`; алерты: `ALERTS_ENABLED`,
`DLQ_ALERT_THRESHOLD`, `SOURCE_CB_ERROR_STREAK`; PQ: `ENCRYPT_DOCUMENTS`,
`PQ_ENCRYPTION_PUBLIC_KEY/PRIVATE_KEY`; прокси: `PROXY_DATAIMPULSE_USER`,
`PROXY_DATAIMPULSE_PASS`, `PROXY_DATAIMPULSE_GEO`, `PROXY_DATAIMPULSE_PORT`
(823 HTTPS / 824 SOCKS5), `PROXY_DATAIMPULSE_STICKY_TTL_SEC`.

## 14. Docker Compose — сервисы

`postgres`, `redis`, `rabbitmq`, `qdrant`, `minio`, `rag_migrate` (Alembic
rag), `rag_worker` (:8101), `rag_api` (:8100), backend, bot, `worker_fetch`,
`worker_send`, `miniapp` (:8080). Тома: pg_data, redis_data, qdrant_storage,
minio_data.

## 15. Запуск и деплой

Локально:
```bash
uv sync && cp .env.example .env   # заполнить BOT_TOKEN, APP_SECRET_KEY, GLM_API_KEY
docker compose up -d postgres redis rabbitmq
uv run alembic upgrade head
uv run uvicorn app.main:app --reload        # :8000
uv run python -m app.bot                    # бот (команды + меню-кнопка)
uv run python -m app.workers.fetch          # cron-планировщик + парсинг
uv run python -m app.workers.send
cd frontend && npm install && npm run dev   # :8080
```

RAG-стек:
```bash
docker compose up -d qdrant minio rag_worker rag_api
uv run --project services/rag python scripts/ingest_kb.py --force
```

Прод-деплой: `docker compose build` → `up -d` → `alembic upgrade head`
(основная + rag БД) → ingest KB → секреты (GLM_API_KEY, RAG_API_TOKEN,
RAG_JWT_SECRET, PQ-ключи) → HTTPS для MINIAPP_URL.

---

# Часть III — журнал реализации (что наворотили)

Все крупные изменения спринтов «ревью → сейчас». Метки: ✅ закрыто и
протестировано; ⏳ код готов, ждёт владельца.

## 16. ✅ Этап 8 — мониторинг и алерты

- `app/services/alerting.py`: алерты в Telegram на `ADMIN_CHAT_ID`, дедуп
  Redis `alert:{key}` с **эскалацией** (повторы глушатся экспоненциально:
  30 мин → 1 ч → 2 ч, cap 2 ч), `ALERTS_ENABLED`.
- DLQ-монитор в `TaskWorker` (глубина `dead_letter` ≥ `DLQ_ALERT_THRESHOLD` →
  алерт, период `HEALTH_CHECK_INTERVAL_SEC`).
- Алерты на открытие/восстановление circuit breaker'а источников и RAG-CB.
- `GET /health/detailed` (postgres/redis/rabbitmq, 503 при недоступной БД),
  `GET /metrics` (пользователи по tier, объявления, уведомления, latency
  источников, `events_24h`) — закрыт `X-Internal-Token`.
- Попутно исправлен латентный баг: `error_streak`/`total_fetched` в
  `source_health` были `None` до первого flush → `TypeError` на первом
  инциденте нового источника (SQLAlchemy column `default=` срабатывает только
  на flush — инстанцировать с явными значениями).

## 17. ✅ Планировщик (schedule_cron)

- `app/services/cron.py` — матчер 5-полей (*/n, диапазоны, списки; dom+dow —
  OR по стандарту cron).
- `IntervalSpec` в `TaskWorker` (интервальные задачи с Redis-локом
  `interval:{name}:{slot}`).
- Fetch-воркер каждую минуту: `schedule_fetch_due` → источники `enabled` с
  подходящим `schedule_cron` ставятся в очередь (лок
  `sched:{source}:{YYYYMMDDHHMM}` от дублей; CB проверяется внутри задачи).

## 18. ✅ Закрытые пункты ревью (факты по коду)

- Fuzzy-дедуп: `app/adapters/common/dedup.py` — VIN + fuzzy-хэш
  (make|model|year|mileage|price), TTL 48 ч, Redis SET NX.
- Circuit breaker источников: `source_health.py` + проверка в fetch-воркере;
  порог `SOURCE_CB_ERROR_STREAK=5` вынесен в env.
- Callback'и уведомлений бот↔backend совпадают (`instant/digest/off`).
- `INTERNAL_TOKEN` выделен из `APP_SECRET_KEY` (обратная совместимость).
- `Dockerfile`, `alembic.ini`, `frontend/*` закоммичены.

## 19. ✅ Продуктовая аналитика (P1-7)

- Таблица `analytics_events` (миграция `0004`) + `app/services/analytics.py` —
  `track()` по принципу alerting: ошибка записи не ломает бизнес-поток.
- Хуки: `user_registered` (первый upsert, `source: bot|miniapp`),
  `search_completed` (только поиск с фильтрами, не просмотр ленты;
  results/filters), `dialog_message` (`/dialog/message`),
  `subscription_activated` (`/payments/success`, tier/days/stars, только
  первая активация — идемпотентность сохранена).
- `lead_created` → воронка search → dialog → lead замкнута.
- `events_24h` по именам — в `/metrics`. Тесты: `tests/test_analytics.py`.

## 20. ✅ Лиды (воронка диалог → обращение)

- `POST /api/leads` (internal, из бота/Mini App: user_id, listing_id?,
  source?, comment) → событие `lead_created`.
- `PATCH /api/leads/{id}` (internal): статус new/in_progress/closed/rejected
  + manager_id — менеджерский процесс.
- Таблица `leads` была в `0001`. Явный `status="new"` при создании
  (тот же класс бага с column `default=`, что и у `SourceHealth`).
  `Literal`-валидация статуса. Тесты: `tests/test_leads.py`.

### 20.1. ✅ Партнёрский контур (P2-11): CPL-продажа лидов

- `partner_accounts` + `partner_api_keys` (миграция `0006`): план/квота,
  статус; ключ хранится только SHA-256 хэшем, сырой `pi_…` показывается один раз.
- Admin API (`X-Internal-Token`): `POST/GET /api/admin/partners`,
  `POST /{id}/rotate` (мгновенная инвалидация старого ключа), `/{id}/revoke`, `PATCH /{id}`.
- Partner API (`X-API-Key`): `GET /me` (ЛК-состояние), `GET /leads` (пул
  квалифицированных лидов без ПД), `POST /leads/{id}/claim` (эксклюзивный
  захват, 409 если занят, 429 по квоте), `POST /leads/{id}/feedback`
  (accepted/returned/junk/deal — доказательство качества лида). Rate limit
  fixed window в Redis (`PARTNER_RATE_LIMIT_PER_MIN`), fail-open при недоступном Redis.
- События воронки: `partner_lead_claimed`, `partner_lead_feedback`.
- `leads`: `partner_id`, `partner_status`, `claimed_at`, `qualification` (JSONB).
- Тесты: `tests/test_partners.py` (28). Веб-ЛК партнёра — следующий шаг (бэкенд готов).

## 21. ⏳ Адаптер Китая Che168 (P2-10) — код готов, ждёт включения

- `Che168HtmlAdapter` (kind='parser') — SSR-парсинг selectolax/lexbor через
  общий `HttpClient` (ретраи, 429, прокси-ротация, MockTransport-инъекция).
- Нормализация: «23.80万» → 238000 CNY, «5.2万公里» → 52000 км, марка
  иероглифами (丰田 → Toyota) — ~50 CJK-марок в `MAKE_MAP`; топливо/КПП
  поиском подстроки (`detect_fuel`/`detect_transmission` — точный
  `normalize_*` на тексте карточки не работает).
- Селекторы/путь списка — в `sources.config` (смена вёрстки правится в БД
  без деплоя); `raw_payload` хранит HTML карточки; VIN=None (скрыт) → общий
  fuzzy-дедуп. Healthcheck = 200 + контейнер списка (капча/вёрстка → False).
- Миграция `0005` вставляет источник `che168` (cron `*/30 * * * *`,
  **enabled=false**), capabilities содержат `use_proxy=true`.
- Зависимость `selectolax` (в venv 1.0.0 — импорт из `selectolax.lexbor`,
  Modest deprecated). Тесты: `tests/test_che168_adapter.py` (13).
- **Для включения:** `alembic upgrade head` → сверить селекторы с живым HTML
  и гео (403 из РБ → DataImpulse, `PROXY_DATAIMPULSE_GEO=cn`) →
  `UPDATE sources SET enabled=true WHERE name='che168'`.

## 22. ⏳ ParserDoctor — ИИ-ремонтник парсеров

Когда источник отвечает, но парсер отдал 0 карточек (смена вёрстки),
fetch-воркер (fire-and-forget) зовёт `app/services/parser_doctor.py`:
живой HTML → GLM предлагает CSS-селекторы (card/title/price/link) →
**локальная валидация** на том же HTML (≥3 карточек, уникальные ID, цены у
половины+) → режим `doctor_mode` в `sources.config`:

| Режим | Поведение |
|---|---|
| `auto` | применить к БД + `AdapterRegistry.reload_source` + алерт «было/стало» |
| `suggest` | только алерт владельцу с проверенным предложением |
| `off` (default) | выключен |

Cooldown Redis 6 ч (`doctor:{source}`, LLM платный). Любой сбой (LLM
недоступен, мусорный JSON, невалидные селекторы, геоблок) не ломает фетч и
не меняет конфиг. Тесты: `tests/test_parser_doctor.py` (12).
**Включение:** `"doctor_mode": "auto"` (или `suggest`) в `sources.config`.

## 23. ✅ Прокси-шлюз DataImpulse (антибан, опционально)

- Формат по официальным докам: `http://login__cr.cn;sessid.X:pass@gw.dataimpulse.com:823`
  (823 HTTPS / 824 SOCKS5; `cr.*` — страна выхода, `sessid.*` — sticky ~30 мин).
- `GatewayProxyPool` (`app/adapters/common/proxy.py`): ротацию IP делает
  провайдер (per request или детерминированное sessid-окно
  `PROXY_DATAIMPULSE_STICKY_TTL_SEC`, 0 = ротация на каждый запрос);
  `mark_bad` не выбрасывает шлюз навсегда — N ошибок соединения подряд
  усыпляют его на cooldown, `get()` возвращает None → HttpClient ходит
  напрямую (обрыв шлюза не убивает парсинг).
- Фабрика `build_proxy_pool(settings, redis)`: user+pass заданы → шлюз,
  иначе статический `ProxyPool` из Redis.
- Подключается в `AdapterRegistry` только к источникам с
  `capabilities.use_proxy=true` (che168 — да, encar API — нет: платный
  трафик не жжётся).
- **Включение:** `PROXY_DATAIMPULSE_USER` + `PROXY_DATAIMPULSE_PASS`
  (+ `PROXY_DATAIMPULSE_GEO=cn` при геоблоке) в `.env`, рестарт воркера.
  Тесты: `tests/test_proxy.py` (11) + 2 в `tests/test_registry.py`.

---

# Часть IV — долги и планы

## 24. ⏳ Ожидает действий владельца

| # | Действие | Зачем |
|---|---|---|
| 1 | `alembic upgrade head` (0004+0005) | аналитика + источник che168 |
| 2 | Сверка che168 с живым HTML + гео | перед `enabled=true`; 403 → DataImpulse `GEO=cn` |
| 3 | Stars: тестовый платёж XTR | код-путь готов, живой оплаты не было |
| 4 | RAG E2E: GLM/PQ-ключи, `ingest_kb.py --force`, контрольный запрос | живой контур ответов |
| 5 | Опционально: `doctor_mode=suggest` | наблюдать за предложениями «врача» без авто-применения |
| 6 | `alembic upgrade head` (+0006) и создание первого партнёра | партнёрский контур: ключ один раз через admin API |

Уже сделано владельцем: **BOT_TOKEN отозван** через @BotFather (перевыпустить
и обновить `.env` перед продом).

## 25. 📋 Бэклог (приоритизирован)

### P2 — кандидаты следующего спринта

1. **Token Bucket Telegram** ✅ реализован (п. 12.6 ТЗ): Redis-бакеты
   (Lua, атомарно) ~30 msg/s глобально + ~1 msg/s на чат, hook в
   `send_telegram_message`; fail-open при недоступном Redis (429 —
   страховка); `TELEGRAM_RATE_*_PER_SEC` (0 = выкл).
2. **SQL-дашборды воронок** ✅ реализованы: `app/services/funnels.py` +
   `GET /api/admin/analytics/funnel` (search→dialog→lead→subscription,
   conv_prev/conv_first %) и `GET /api/admin/analytics/retention`
   (когорты по дню `user_registered`, D1/D7).
3. **Веб-ЛК партнёра** ✅ реализован (`/partner/*` во фронтенде): вход по
   ключу, дашборд, пул + claim (409/429), мои лиды + фидбек, API-ключ;
   ротация ключа — через менеджера/админку. Бэкенд: `?mine=1` в `/api/partner/leads`.
4. **Биллинг партнёров** ✅ реализован (миграция `0014`): CPL-списание
   при claim (индивидуальный `cpl_rate` или тариф плана
   `PARTNER_CPL_RATES`), журнал `partner_transactions` (+topup /
   −charge_lead, balance_after), при исчерпании — suspended + алерт
   (дедуп 24ч), пополнение `POST /api/admin/partners/{id}/billing`
   снимает suspended; claim без денег → 402. Баланс и цена лида — в
   `/api/partner/me` и `/api/partner/billing`.

### P3 — стратегическое

3. LLM cost tracking: токены/стоимость GLM в `DialogSession` или отдельной
   таблице, сводка в `/metrics` (unit-экономика).
4. Ротация PQ-ключей и разделение доступа к приватному ключу.
5. Push ветки `refactor/monorepo` (rag_service: uv-workspace, 361 тест —
   ветка готова локально).
6. Публичный HTTPS `MINIAPP_URL` для прода + проверка кнопки меню.
7. Рефакторинг веб-авторизации: `telegram_id` → nullable, отдельный PK
   (синтетические отрицательные id — коллизии после ~30k веб-юзеров).

## 26. Тесты и качество

- `uv run pytest` → **504 passed** (vitest: +9): LLM-клиенты, RAG-клиент (fallback, CB,
  кэш), агент, уведомления, адаптеры (контракты, дедуп, che168), auth,
  PQ-крипто, мониторинг/алерты, cron/планировщик, аналитика, лиды,
  ParserDoctor, прокси (GatewayProxyPool, фабрика), партнёрский контур
  (ключи/ротация, эксклюзивный claim, квоты, rate limit, фидбек),
  S3/MinIO storage, token bucket, воронки (funnel/retention),
  партнёрский биллинг (CPL-списания, topup, suspended/reactivate).
- `ruff check` — чисто; mypy настроен. Frontend: `tsc --noEmit` + `vite build`.
- Тест-хелперы: `FakeSession` (строковые PK, `rollback()`, `get`),
  `FakeRedis` (`ttls`), `MockLLMClient` (`script`, `.calls`).

## 27. История коммитов (последние)

| Commit | Что вошло |
|---|---|
| `1c898e4` | ТЗ (TZ.md) |
| `b448c76` | RAG-микросервис, /api/ai, AI-поиск в Mini App, inline-меню, compose |
| `a1d39c7` | Регистрация/логин сайта, Login-экран, миграция 0003 |
| `46a7eb4` | GLM основной, БЗ РБ/РФ/СНГ, TG-кнопка WebApp, PQ-шифрование |
| `7489aec`, `7a29eb3` | Аналитика (P1-7) + лиды: хуки, `POST/PATCH /api/leads`, `events_24h` |
| `3dbe08e` | Che168-адаптер (P2-10): парсер, нормализация 万, миграция 0005 |
| `6a3f934` | ParserDoctor: GLM-ремонт селекторов, doctor_mode, reload_source |
| `ffb50dd` | Прокси-шлюз DataImpulse (GatewayProxyPool, use_proxy) |
| `ffe4a0e` | Партнёрский контур (P2-11): ключи с ротацией, claim лидов, квоты, rate limit |
| `8dc790f` | Token Bucket Telegram (P2-12): 30 msg/s глобально + 1 msg/s на чат, Redis/Lua |
| `c987714` | SQL-дашборды воронок (P2-13): funnel + retention D1/D7 admin-эндпоинты |
| (см. git) | Биллинг партнёров (P2-14): CPL-списания, баланс, suspended/reactivate, миграция 0014 |
| (отд. репо) | rag_service monorepo: 361 тест, ветка `refactor/monorepo` (не запушена) |
| `15400e9` | Encar: новый API-контракт (sr/inav/cursor, q с `_.`, корейские фасеты, FormYear/Year, USD→KRW) |
| `10dcceb` (PR #1) | Микросервисы: gateway + 6 доменных сервисов (профили mono/micro), auth refresh, багфиксы (`max→le`, `user.telegram_id`, html.escape) |
| (этот merge) | Мерж PR #1 + правки: env-оверрайды DATABASE_URL/REDIS_URL/RABBITMQ у 6 svc, `depends_on required:false` (bot/miniapp в micro), mvp.sh `--profile mono`, `/metrics` вынесен в `app/api/metrics.py` (admin + gateway-прокси), ParserDoctor: инъекция redis в send_alert + изоляция Telegram в тестах (442 теста, ruff clean) |
| (этот коммит) | KCar: живой API (`/bc/search/list`, AES-enc из бандла фронта, wr_-фильтры, top-N) + персистентность доктора: admin PUT сливает config/capabilities/retry_policy, mvp.sh создаёт источник только при отсутствии; MAKE_MAP + корейские марки (447 тестов, ruff clean) |

## 28. Ревью PR #1 (feat/microservices) — найденные и исправленные проблемы

1. **compose**: у 6 доменных сервисов (identity/catalog/billing/partners/dialog/admin)
   не было `environment:`-оверрайдов `DATABASE_URL/REDIS_URL/RABBITMQ_URL` — при
   `localhost` в `.env` они не связывались бы с инфраструктурой (тот же баг, что
   ранее исправлен для backend/workers).
2. **compose**: `bot` и `miniapp` зависели от `backend` (профиль mono) — в профиле
   `micro` не стартовали; заменено на `depends_on ... required: false` для
   backend+gateway (Compose ≥2.20).
3. **mvp.sh**: переведён на `--profile mono` (иначе сервисы с профилями не поднимутся).
4. **`GET /metrics`** (ТЗ п.10) жил только в `app/main.py` — в режиме micro gateway
   (только `/api/*`) отдавал 404: роут вынесен в `app/api/metrics.py`, подключён
   монолитом и admin-сервисом, gateway проксирует `/metrics` → admin.
5. **ParserDoctor**: `send_alert` ходил в реальный Redis через `_get_redis()` —
   тесты падали при остановленном Docker (9 падений); добавлена инъекция
   `redis=` в `send_alert`, проброс через `_repair`/`_triage_result`, в тестах
   изолирован реальный Telegram (`_send_telegram` → stub).

Конфликт мержа (docker-compose.yml) разрулен: профили PR + env-оверрайды локальных
коммитов сохранены.

## 29. KCar: живой API + фикс персистентности ParserDoctor

1. **kcar.com — источник снова рабочий.** Старый контракт
   (`api.kcar.com/dst/...`, GET `?page=&count=`) мёртв (404). Восстановлен
   актуальный: `POST https://api.kcar.com/bc/search/list`, тело
   `{"enc": base64(AES-CBC(JSON))}` — ключ `SKFJ2424DasfaJRI`, iv
   `sfq241sf3dscs321` (воспроизведено из фронтового бандла `_nuxt/*.js`,
   `setEncDef`), PKCS7. Ответ `{"data": {"totalCnt", "rows": [...]}}`.
   Пагинации в API нет (page/pageNo/... игнорируются — проверено запросами):
   адаптер берёт top-N (`limit`, по умолчанию 100) свежих объявлений,
   `has_more=False`. Фильтры `wr_*` (make/model/годы YYYYMM/цена в манах/
   пробег/топливо — код «001»=가솔린 подтверждён живым ответом; остальные
   коды не шлём). Карта полей: carCd/prc(ман)/milg/prdcnYr/mnuftrNm/
   modelNm/grdNm+grdDtlNm/carWhlNm/fuelNm/trnsmsnNm/cntrRgnNm/lsizeImgPath;
   карточка: `/bc/detail/CarInfoDtl?carCd=<carCd>`. Live-check: 100 items,
   Hyundai с 2018 → total 7480.
   `HttpClient` получил `post/post_json` (общий `_request` с ретраями/429).
2. **Персистентность ParserDoctor.** Корень бага «ремонт слетает после
   рестарта»: `PUT /api/admin/sources/{name}` замещал `config` целиком, а
   `mvp.sh up --parse` при каждом старте перезаписывал конфиг
   `{"page_size": 20}`. Исправлено дважды:
   - admin PUT теперь СЛИВАЕТ словари `config/capabilities/retry_policy`
     (ключи из body перекрывают, остальные сохраняются); скалярные поля —
     как раньше;
   - `mvp.sh` создаёт источник только если его ещё нет (GET `/sources`
     → PUT при 404), существующий конфиг не трогает.
   Регресс-тесты: `test_upsert_source_merges_config`,
   `test_upsert_source_new_creates_with_body_config`.
3. **normalize**: MAKE_MAP дополнен корейскими именами марок из живых API
   (벤츠→Mercedes-Benz, 포르쉐→Porsche и т.д. — иначе kcar/encar значения
   не нормализовались).
4. **che168** (вне этого коммита): desktop за Ali WAF + EdgeOne captcha;
   обход уровня 1 (acw_tc + solve-куки, Baidu-referer) даёт живой HTML
   нестабильно (вероятностный WAF), уровень 2 — капча. Нужны прокси
   (DataImpulse creds пустые) или headless-браузер; адаптер триажит
   капчу как ban-исход.

**447 тестов зелёные, ruff clean.**

### 30. E2E-валидация персистентности + живой синк kcar в стеке (коммит «fix: e2e hardening…»)
1. **E2E e2e-проверка фикса персистентности пройдена**: сломанный→«отремонтированный»
   конфиг encar (`doctor_note` в config) + включённый kcar пережили
   рестарт `mvp.sh up --parse` — ни PUT-reseeding, ни перезапись конфига.
2. **Фикс найден по ходу e2e**: строки источников с NULL `retry_policy`/
   `capabilities`/`config` в БД роняли `GET /api/admin/sources` целиком
   (500, ValidationError в SourceConfig) — добавлен `field_validator`,
   None → дефолты. Тест `test_source_config_null_db_fields_to_defaults`.
3. **`HttpClient._request`: таймауты теперь ретраятся** — kcar отдаёт тело
   списка медленно (httpx.ReadTimeout после 200), раньше не ловился вовсе.
   Добавлены `httpx.TimeoutException` в retryable с backoff 2^attempt.
   Тесты: `tests/test_http_client.py` (успех после ретрая; исчерпание →
   FetchError).
4. **kcar введён в бой в стеке**: источник включён (enabled, cron */15,
   timeout 60s, page_size 100), образ worker_fetch пересобран (в старом
   был мёртвый эндпоинт `/dst/...`), circuit breaker сброшен.
   Live: `POST /bc/search/list` → **100 новых объявлений сохранено**
   (в БД kcar=102, encar=44).
   Нюансы эксплуатации: circuit breaker (5 ошибок подряд + cooldown 10м)
   сбрасывается `UPDATE source_health SET error_streak=0 ...`; воркер
   читает список источников на старте — после включения источника нужен
   `docker compose restart worker_fetch`.

**450 тестов зелёные, ruff clean.**



### 31. UX-ремонт бота: панель диалога, мини-апп, Китай, кнопочный фоллбэк
1. **Вал сообщений → «живая» панель**: каждый ход ассистента (и каждый шаг
   кнопочного фоллбэка) редактирует предыдущее сообщение на месте
   (`panel_msg_id` в FSM), новые сообщения — только при первом шаге.
2. **JSON-критерии → человекочитаемый список**: `format_criteria` с
   русскими лейблами, ценами с пробелами и странами-флагами
   (`{'country': 'CN'}` → «Страна: 🇨🇳 Китай»). Убран `json.dumps` из текста.
3. **Китай вернулся**: опции стран генерировал LLM без ограничений —
   в SYSTEM_PROMPT добавлен канонический список (Корея/Китай/Германия/
   США/Япония) и требование давать опции строго из него.
4. **Мультивыбор больше не ломает ход**: кнопки опций несут токен хода
   (`dlg:opt:{turn}:{idx}`), клик по устаревшей кнопке отвечается
   «устарела» и игнорируется.
5. **Кнопки всегда при активном ассистенте**: «🔎 Запустить поиск»
   появляется и по эвристике заполненности (make/model + цена + страна),
   а не только когда LLM решил; плюс «📱 Мини-приложение» и «🔄 Сбросить».
6. **Мини-апп доступен и на http**: `MINIAPP_URL` без https (локальная
   демка) раньше давала пустую клавиатуру — теперь url-кнопка
   (браузер); web_app-кнопка — только для https.
7. **Багфиксы**: (а) клик по опции слал в диалог `user_id` **бота**
   (id брали из `cb.message.from_user`) — ход диалога шёл в чужую
   сессию, «после выбора страны ничего не происходило»; теперь
   `user_id` передаётся явно из `cb.from_user`. (б) в кнопочном
   фоллбэке вообще не было обработчика `btn:`-колбэков — инлайн-кнопки
   не работали; добавлен `btn_click` с маршрутизацией по полю.

Тесты: `tests/test_bot_dialog_ui.py` — 7 шт (формат, эвристика,
токены хода, кнопка поиска по эвристике, http/https мини-апп).
**457 тестов зелёные, ruff clean.** Бот пересобран и задеплоен в стек.

### 32. Инцидент: AI-режим 502 + кнопка мини-аппа не появлялась
1. **502 на /api/dialog/message**: в mono-стеке одновременно с backend был
   поднят gateway (micro-профиль) с DNS-алиасом `backend` — имя `backend`
   резолвилось в два контейнера (round-robin), запросы бота случайным
   образом уходили в gateway и зацикливались → 502. Фикс: gateway
   остановлен (`docker compose rm -sf gateway`); в mono он не нужен.
   ПРАВИЛО: gateway и backend одновременно не поднимать — алиас конфликтует.
2. **Кнопка мини-аппа**: `_miniapp_url()` фильтровал не-https в пустоту,
   из-за чего fallback на url-кнопку в `_miniapp_button` был недостижим.
   Теперь `_miniapp_url` возвращает сырой URL, схема проверяется в
   `_miniapp_button` (https → web_app, http → url-кнопка).

### 33. Исследование CarAPIS (проверено вживую, не интегрировано)
Источник: https://carapis.com/ru/developers/ — каталог 2.8M объявлений
(~80 площадок, единая JSON-схема + оценка относительно рынка).
- **MCP-сервер (ключевая находка)**: `https://mcp.carapis.com/mcp/` —
  бесплатно, БЕЗ ключа и без дневного лимита. Инструменты: search
  (бренд/модель/годы/цена USD/пробег/площадка, limit≤25), get по UUID,
  facets, market_summary (min/медиана/max по срезу).
- **Покрытие (facets по source)**: encar 113 633, kcar 8 780.
  **che168 и китайских площадок НЕТ.**
- **Качество данных**: поля id/source/brand/model/trim/year/mileage/
  fuel_type/transmission/body_type/color/price_usd + valuation
  {estimated_usd, low/high, confidence_0_1, percentile_rank,
  comparable_count, is_undervalued}. Пример: Palisade 2022, 48.8k км,
  $18 394 при оценке рынка $24 842 (undervalued, 170 компарабелей).
- **Ограничения**: в бесплатном MCP нет URL и ID исходного объявления
  (только внутренний UUID) → для синхронизации непригоден; поиск
  медленный (~30 с на запрос) — не годится в реальном времени;
  REST-тариф: 100 запросов/день бесплатно (с URL объявлений),
  Production 5k/день — цена в кабинете.
- **Вывод**: парсеры encar/kcar не заменять. Ценность — фича «оценка
  рынка» (undervalued-лоты, медианные цены при финализации критериев)
  или инструмент AI-агента. Решение по интеграции отложено.

## 34. MVP-разворот: Корея-only, менеджер Артём, фото-карточки, калькулятор, nginx+SSL

Разворот прототипа под главную идею: бот → WebApp (пользователь выбирает
понравившийся UI/UX). Всё не-корейское выпилено.

### Backend
- **Калькулятор доставки Корея → РФ** (`app/services/delivery.py`,
  `app/api/calc.py`): POST /api/calc/delivery {price_usd, engine_cc, year} →
  смета «под ключ» (авто, море, брокер, СБКТС/ЭПТС, пошлина по возрастной
  группе и €/см³, сбор, утильсбор); POST /api/calc/compare — сравнение
  вариантов с сортировкой по итогу. Ставки — константы модуля (курс 92 ₽/$,
  1.08 $/€, море 1800 $, брокер 250 $, СБКТС 350 $; льготный утильсбор
  3400/5200 ₽, >3000 см³ — полная 136 200 ₽).
- **Лента только Корея**: GET /api/listings без `source` → encar+kcar.
- **AI-агент**: новый промпт — «Артём, менеджер по авто из Кореи»
  (проактивный, Корея-only, расчёты — только через инструмент); новый tool
  `calc_delivery` (schema в tools.py, исполнение в agent.py).

### Бот
- **Фото-карточки**: после «Поиск запущен» (dlg:finalize) — топ-5 объявлений
  фото-хотлинками (sendPhoto по URL источника, ничего не храним), caption +
  кнопки «🔗 Открыть объявление» / «🚚 Доставка и растаможка» / «📱 Приложение».
  Фолбэк на текстовую карточку, если фото источника недоступно.
- **Калькулятор в боте**: /calc [цена $ объём год], кнопка «🧮 Калькулятор» в
  меню, callback calc:{id} прямо с фото-карточки (KRW/EUR → USD по фикс-курсу).
- **Кнопочный fallback**: шаг «страна» удалён (4 шага, всегда KR).
- /start: приветствие менеджера, Корея-only.

### Mini App (frontend)
- Новый экран **/calc** (screens/Calc.tsx): форма (цена/объём/год) → градиентная
  карточка «Итого под ключ» + разбивка + дисклеймер; преилл из query
  (?price=&cc=&year=). Кнопка «Доставка и растаможка» в карточке объявления.

### nginx + самоподписанный SSL
- frontend/nginx.conf: 80 + 443 ssl (прокси /api → backend:8000);
  frontend/entrypoint-ssl.sh — генерация self-signed при старте
  (SAN = DNS:localhost + IP:$NG_HOST_IP); compose: 8443:443,
  NG_HOST_IP=${LAN_IP}. .env: MINIAPP_URL=https://10.132.137.38:8443,
  LAN_IP=10.132.137.38.
- ⚠️ **Ограничение Telegram**: web_app-кнопка требует ДОВЕРЕННЫЙ сертификат —
  с self-signed нативный WebApp в клиентах Telegram может не открыться
  (url-кнопки работают всегда). Для полноценного web_app нужен домен +
  Let's Encrypt (следующий шаг).

### Проверки
- 463 теста зелёные (включая новые tests/test_delivery.py), ruff чист.
- Живьём: https://localhost:8443 → 200; /api/listings → только encar/kcar;
  /api/calc/delivery и /api/calc/compare считают; бот с MINIAPP_URL=https.
- Грабли: CRLF в entrypoint-ssl.sh (Windows) → «no such file or directory»;
  лечится `sed -i 's/\r$//'` перед сборкой.

## 35. Полное тест-покрытие MVP-калькулятора (+26 тестов → 489)

- **tests/test_calc_api.py** (9): /api/calc/delivery (200 + числа: пошлина
  5832 $, 7 компонентов, тотал > цены, ₽/$ согласованы; default engine_cc;
  4×валидация 422), /api/calc/compare (сортировка по итогу, пропуск битых
  items, автотайтл, пустой список → 422).
- **tests/test_bot_calc.py** (14): _price_usd (KRW/EUR/USD/мусор),
  _format_calc_out (7 строк, группировка «18 000 $»), _miniapp_button с path
  (https → web_app с query, http → url-фолбэк), _miniapp_kb,
  _car_card_kb (🔗/🚚/calc:{id}, без url/miniapp), build_listing_caption
  (KRW-цена, отсутствие цены, лимит 1024).
- **tests/test_agent.py** (+3): tool calc_delivery — полная разбивка 7 строк
  (агент не выдумывает цифры), битые аргументы → просьба уточнить,
  схема tool для LLM (required price_usd+year).
- **Найденные тестами баги (исправлены)**: «18,000 $» вместо «18 000 $»
  в сметах бота (_format_calc_out) и format_breakdown — запятые десятичного
  разделителя локали f-string не заменялись на пробелы.

## 36. Сравнение «под ключ» end-to-end + тестовая база фронтенда (+15 тестов → 504 py + 9 vitest)

**Фича сравнения ( ранее только API):**
- бот: `/calc A vs B` (разделители `vs`/`;`/`|`/перенос) → `POST /api/calc/compare`, медали + дельта; `client.compare_delivery`
- бот: меню «⚖️ Сравнить» → хинт + миниапп `/compare`; команда `/calc` добавлена в set_my_commands
- вебапп: экран `/compare` (2–4 варианта, бары, «лучший итог»), кнопка из калькулятора с сид-параметрами, deep-link `?v=18000x2000x2022,...`

**Тесты:**
- `tests/test_bot_calc.py` +9: интеграция `cmd_calc` (один вариант, 2 числа, «18 000», vs-сравнение, фоллбэк при недоступном бэкенде, мусор→help), `menu:compare`/`menu:ai`; фейки `FakeMsg`/`FakeCb`
- фронтенд: vitest (`npm test`), `src/lib/fx.ts` — общие чистые хелперы (`toUsd`, `digits`, `parseQueryVariants`); Listing/Compare переведены на него; `fx.test.ts` 9 тестов
- пойманные тестами баги: regex `\d[\d ]*` склеивал числа в одно (парсер вариантов переписан + склейка тысяч «18 000»); `parseQueryVariants` терял год в коротком формате `18000x2022`

Итог: `pytest` → **504 passed**, `vitest` → **9 passed**, ruff/mypy clean, bot+miniapp пересобраны.

## 37. Бот «идеал»: ссылка → карточка, антифлуд, error-handler (+16 тестов → 520)

- **Ссылка на объяву → карточка**: `GET /api/listings/by-url` (нормализация: схема/www/m./слеш), `client.get_listing_by_url`; `fallback_text`: URL → фото-карточка с кнопками (расчёт/миниапп), не найдено → AI. `/help` с примерами `/calc` и «скиньте ссылку»
- **Антифлуд**: `app/bot/throttle.py` — `FloodGate` (скользящее окно 15 событий/10 с, чистая функция времени) + `FloodMiddleware` на message/callback_query (дроп, варн раз в окно)
- **`@dp.errors`**: лог + «😔 Что-то пошло не так…» вместо тишины; polling не падает
- Тесты +16: throttle (8: лимит, окно, независимость юзеров, миддлварь), by-url (4), fallback-ссылки (4); test-найденный баг: первый `should_warn` блокировался дефолтом 0.0
- Живая проверка: `by-url?url=https://encar.com/e2e-1` → 200 BMW 520i; 404 для отсутствующих

## 38. AI-анализ стоимости: LLM-разбор + график, монетизация 1/нед (+33 теста → 553)

- **Сервис** `app/services/price_analysis.py`: comps (та же make+model, год ±2, до 30 шт, медиана/min/max), история цен, промпт на строгий JSON; `parse_llm_json` терпит ```-заборы; LLM упал/мусор → статфолбэк (вердикт по медиане ±7%, диапазон 0.93–1.07 median). Кэш разбора общий на листинг (LLM дорогой)
- **Модель + миграция 0015**: `price_analyses` (listing_id, user_id, verdict, fair_min/max, summary, payload JSONB, created_at; 2 индекса). Монетизация: free = 1 разбор (user, listing) в 7 дней, pro/business = безлимит (`quota_left`, `QuotaExceeded` → 403 `quota_exceeded`)
- **API** `app/api/analysis.py`: `POST /api/listings/{id}/analysis` (JWT, LLM+сохранение), `GET` (кэш, без LLM), `POST /api/bot/listings/{id}/analysis` (X-Internal-Token, body user_id) — оба роутера в main.py
- **Бот**: кнопка «🤖 Анализ цены» на фото-карточке (`analyze:{id}`, рядом с 🚚), хендлер: «Анализирую…» → вердикт (🟢/🟡/🔴), диапазон, отклонение %, факторы, медиана рынка + кнопка миниапп `/listing/{id}` (график). 403 → апселл Pro + миниапп; backend down → вежливое. `client.analyze_price` — своя реализация (120 с, 403 ≠ ошибка). `/help` дополнен
- **WebApp/сайт**: секция «🤖 AI-анализ цены» в Listing.tsx: GET кэша при открытии → сразу график; иначе кнопка «Анализировать (1 бесплатно в неделю)» → POST; 403 → замок + «⚡ Безлимит в Pro» → /profile. `lib/analysis.ts` (типы, `deltaPct`, `buildChartData` — чистые), `ui/PriceChart.tsx` — SVG-график: линия истории, точка текущей цены, зелёная зона рыночного диапазона (без chart-библиотек). CSS-блок `.analysis*`, `.price-chart*`
- **Тесты**: `tests/test_price_analysis.py` (10: парсинг JSON, пороги вердикта, квоты, LLM/статфолбэк), `tests/test_analysis_api.py` (6: POST 200/403/404, pro безлимит, GET кэш/None; FakeSession.get + `.first()` в conftest), `tests/test_bot_analysis.py` (7: формат, кнопка карточки, хендлер ок/квота/down/мусор), `frontend/src/lib/analysis.test.ts` (9: deltaPct, buildChartData, VERDICT_LABEL). **Итог: 543 pytest, 18 vitest; ruff чисто; mypy не хуже базы; фронт собирается**

## 39. Шлифовка UI/UX анализа (график, состояния, формат в боте) (+1 тест → 544)

- **График** (`PriceChart.tsx`): градиентная заливка под линией, подпись текущей цены у точки, даты первой/последней точки (дд.мм), компактные подписи шкалы (`formatCompact`: 18,4 млн / 950 тыс), пульс точки; иначе — подсказка «истории цены пока мало» (не прятать блок молча)
- **Секция анализа** (Listing.tsx): состояние загрузки — спиннер + скелетоны («Сравниваю с рынком…»), pill-бейдж отклонения (▼/▲ с цветом), шкала справедливого диапазона с анимированным маркером цены (`fairPosition`), строка меты «разбор от {дата} · нейросеть/статистика» (+`created_at` в payload/API) и «🔄 Пересчитать»; при квоте — «доступен снова через несколько дней»; fade-in секции
- **Бот**: `_format_analysis` реструктурирован — цена (💵), рынок (📊 count+медиана), год в заголовке, стрелка 📉/📈 у отклонения, футер с квотой и Pro
- **Тесты**: +formatCompact/fairPosition (analysis.test.ts 22 vitest), +test_format_analysis_title_with_year_and_money_lines; пойман баг formatCompact (18,4 млн → «18 млн»). **544 pytest / 22 vitest, ruff+mypy чисто, бандл на 8443 обновлён**

## 40. Боевое развёртывание MVP: секреты, fail-fast, deploy-кит (+4 теста → 548)

Цель — полный prod-контур: VPS + docker compose (mono) + Caddy (авто-Let's Encrypt).

- **Ротация секретов (INC-001, INC-002 ЗАКРЫТЫ)**: `APP_SECRET_KEY` был длиной 9 символов
  (= подпись JWT + fallback internal-токена). Сгенерированы: 64-hex `APP_SECRET_KEY`,
  отдельный 64-hex `INTERNAL_TOKEN`, сильный `DB_PASSWORD` (+`ALTER USER` в живой БД —
  volume уже инициализирован). backend/bot пересобраны и перезапущены; внутренний API:
  неверный токен → 401, верный → 200. Старые JWT инвалидированы (перелогин через initData).
- **Fail-fast (INC-004 ЗАКРЫТ)**: `Settings._prod_secrets_required` — при `APP_ENV=prod`
  падаем на старте, если ключ = дев-дефолт или короче 32 символов (`tests/test_prod_secrets.py`).
- **INC-005 (прод)**: в боевом оверрае TLS терминирует Caddy (авто-LE по `$DOMAIN`),
  self-signed 8443 остаётся только в dev.
- **Deploy-кит `deploy/`**:
  - `docker-compose.prod.yml` — оверрайд: Caddy на 80/443 → miniapp → /api; порты инфры
    (postgres/redis/rabbitmq) и backend сняты с публикации (`!reset []`); `restart: always`;
    ротация логов json-file 10 MB × 3; `APP_ENV=prod`, публичный `MINIAPP_URL=https://$DOMAIN`.
  - `Caddyfile` — авто-сертификат, gzip, `/health`.
  - `bootstrap.sh` — VPS одной командой: Docker, генерация `.env` с боевыми секретами,
    UFW (наружу только 22/80/443), подъём, миграции, cron бэкапа.
  - `backup.sh` / `restore.sh` — pg_dump daily (14 копий) + восстановление.
  - `DEPLOY.md` — ранбук: DNS → bootstrap → проверка LE → обновления → бэкапы → мониторинг.
- **Тесты**: тесты internal-API переведены с `get_settings().app_secret_key` на
  `deps_internal.internal_token()` (единый источник истины — теперь ротация INTERNAL_TOKEN
  не ломает их). **548 pytest (+4), ruff чисто, mypy = база**
- **Live**: стек на новой машине поднят с нуля после рестарта Docker — backend healthy,
  бот поллит, miniapp 200. Для выхода в прод: VPS + домен + `bootstrap.sh` (DEPLOY.md).

## 41. Скрипты запуска + гостевой режим сайта (+2 pytest → 550, +18 vitest → 40)

- **Скрипты запуска**: `start.bat` (Windows) / `start.sh` (Linux) — инфраструктура →
  `--profile mono up -d --build` → миграции → ожидание /health → подсказка URL.
  Для VPS остаётся `deploy/bootstrap.sh`.
- **Гостевой режим (незалогиненный посетитель может всё потрогать)**:
  - Бэк: `POST /api/auth/guest` — временный юзер («Гость», синтетический id < 0,
    без email/пароля, явно role=buyer/tier=free/is_active) → обычная JWT-пара.
    Гость = free-юзер: лента, фильтры, карточка, **AI-разбор цены 1/нед**,
    калькулятор, избранное. Нет только Telegram-уведомлений и Pro.
  - Фронт: вне Telegram `authorize()` → null → тихий `guestLogin()` → полный UI
    без формы входа; скип онбординга; дисмиссабельный баннер «Демо без регистрации»
    со ссылкой /login; роут `/login` (только для гостя) — LoginScreen, после входа
    гостевые токены заменяются (`clearGuest`); в Профиле — гостевой блок
    «Войти / Регистрация».
  - Публичные GET и так открыты: лента/карточка/кэш разбора/калькулятор.
- **Тесты сайта (vitest)**: `vitest.config.ts` (jsdom + react-плагин);
  dev-deps jsdom + @testing-library/react. Новое: `api/client.test.ts` (токены,
  Bearer, 204, ApiError/detail/statusText), `api/auth.test.ts` (Telegram-контекст,
  гостевой вход/выход, webLogin/webRegister, 409), `App.guest.test.tsx`
  (компонентные: гость→лента+баннер, крестик баннера, гостевой /login,
  у TG-юзера гостя нет). Стабы matchMedia/IntersectionObserver. **40 vitest / 550 pytest**

## 42. Ставки 2026 по НПА (мощность >160 л.с., сбор ПП № 1637) + полировка вёрстки

- **Проверка НПА на 16.10.2026** (первоисточники: alta.ru, ПРАЙМ, Живая статья
  auto.ru/mag/article/utilizacionnyy-sbor с действующими таблицами):
  - **Утильсбор**: с 01.12.2025 (ПП № 1291, действующая ред.) коэффициент —
    прогрессивная шкала по **мощности** (л.с.) в сочетании с объёмом; база
    20 000 ₽. Льгота физлица (3400/5200 ₽) — только объём ≤3000 см³ **и
    мощность ≤160 л.с. включительно**. Индексация коэффициентов с 01.01.2026
    учтена (шкала до 01.01.2027, дальше +10–20% по Минпромторгу). Контрольный
    пример из статьи сходится: 2,5 л / 235 л.с. / <3 лет → 120,12 × 20 000 =
    2 402 400 ₽.
  - **Сбор за таможенные операции**: ПП № 342 мёртв; с 2025 — ПП № 1637
    (28.11.2024), с 01.01.2026 — ред. ПП № 1638 (23.10.2025): 1231 / 2462 /
    4924 / 13 541 / 18 465 / 21 344 / 49 240 / 73 860 ₽ (порог партии 10 млн ₽).
  - Пошлины ЕТТ физлиц (Решение ЕЭК № 54) — без изменений.
- **Код**: `delivery.py` — `power_hp` в `calc_delivery` (+ `util_category`,
  `util_note` при неизвестной мощности), шкалы `_UTIL_SCALE_*` (границы
  диапазонов = конец полосы, офф-бай-ван выловлен тестом с примером auto.ru),
  `CLEARANCE_FEE_BRACKETS` 2026. API `power_hp` (+валидация 10..2000), бот
  (`/calc`, tool LLM), миниапп — поле «Мощность, л.с.» + плашка-предупреждение.
- **Вёрстка**: инлайн-`opacity` у чипа сравнения убран → `.chip:disabled`;
  новая `.calc-warn` (янтарная плашка) с dark-темой; guard-тесты в
  `styles.test.ts`. Починен Docker-сборк миниаппа: `tsconfig.json` теперь
  исключает `*.test.*` (в контейнере нет `@types/node`, tsc падал на
  `node:fs` в styles.test.ts).
- **Тесты**: `test_delivery` (шкала по мощности: 120,12/177,60/45,00; >3000 см³
  129,20/197,81/222,84; сбор 2026 по 8 брэкетам; legal_refs — 5 позиций),
  `test_calc_api` (полный утиль при 235 л.с., валидация power_hp, ПП 1637),
  `test_agent` (power_hp в tool-схеме). **559 pytest / 62 vitest, ruff чисто.**
- **Live на стенде**: backend+miniapp пересобраны; POST /calc/delivery
  235 л.с. → «полный» 2 402 400 ₽; без мощности → льготный 5200 ₽ + note;
  сбор 13 541 ₽; в бандле миниаппа есть `.calc-warn`.

## 43. Редизайн миниаппа + лимит Free 3 AI/день + регион РБ/РФ

- **Лимиты**: Free `ai_daily_limit` 20 → **3** (`subscription.py`), Pro 200 — без
  изменений. Тесты не завязаны на 20 — 559 pytest зелёные.
- **Галерея карточки**: слайды 16/10 «под ключ», клик по фото/кнопке ⤢ →
  **лайтбокс** (тёмный фон 94%, `object-fit: contain` — фото целиком без обрезки,
  стрелки, Esc/←→ с клавиатуры, счётчик, клик мимо — закрыть, лента миниатюр
  до 6 + «+N»). Раньше фуллскрина вообще не было.
- **Вёрстка**: единый контейнер `.screen` (760px, авто-центр), секции со
  скруглением 16px и явной тенью, ПК-раскладка карточки 2 колонки (галерея+инфо |
  sticky-действия), лента 2→3 колонки на ≥1280px, доопределён отсутствовавший
  `@keyframes fadeIn`. Дедуп старого блока `.gallery`.
- **AI-чат** (`AiSearch.tsx`): переписан на пузыри (юзер справа акцентный, агент
  слева карточкой), typing-анимация, чипсы быстрых ответов, sticky-композер
  (textarea + Enter=отправить), интро-карточка.
- **Фильтры**: кроме марки — модель, год от/до, цена $ от/до, пробег, топливо,
  КПП, кузов, привод (`SearchCriteria` бэка всё поддерживает); пары инпутов
  `.filter-pair`, нативные селекты, понятная подпись критериев, обработка
  ошибок (пустая форма, лимит тарифа).
- **Оплата подписки**: обработаны все статусы `openInvoice` (paid/failed/
  **cancelled**/pending) + ошибки API (503 «платежи недоступны», 401/403
  «сессия истекла»), вне Telegram — понятное сообщение, что Stars оплачиваются
  только в TG. Сообщения — стилизованные плашки `.profile-msg`.
- **Регион РБ/РФ** (`fx.ts`): `getRegion/setRegion` (localStorage + авто по
  локали), курсы USD→₽/Br (фикс MVP), `priceChain()` — **$ → локальная валюта →
  цена источника** в карточках и на странице объявления; переключатель региона
  в Профиле; в калькуляторе для РБ — отдельная заметка (платежи ЕАЭС одинаковы,
  регистрация в ГАИ РБ — свои правила). **62 vitest / 559 pytest, tsc чисто.**


## 44. Лейаут-фиксы и пагинация ленты (2026-10-17, `d195c33`, `16c2607`)
- **Гигантские фото**: `.gallery__slide` `min-width:0` — img во flex не сжимался
  ниже натуральной ширины (1080px) и вылезал за трек; лайтбоксу — страховка
  `min-width/height:0`.
- **Сжатая лента на ПК**: гостевой баннер сжимал `.app-content` до ~430px;
  `flex-wrap` на shell + баннер `flex-basis:100%`, контенту `min-width:0`.
- **Пагинация**: при ошибке догрузки лента молча останавливалась — добавлены
  инлайн-ошибка с «Повторить» и кнопка «Показать ещё» (`.feed-more`) как
  фолбэк к IntersectionObserver; тесты `Feed.test.tsx`.
- Проверено Playwright-аудитом стенда (мобайл 390 / ПК 1440): слайд=ширине
  трека, лента 3×381px, скролл до 207/207.
- **Стенд**: лишний `gateway` (compose-алиас `backend`) давал 502 через
  round-robin DNS — остановлен; миниапп обновляется только `build miniapp`
  (образ, не `restart`).

## 45. Расширенные фильтры ленты (2026-10-17)
- Чипсы-пресеты: 3 → 9 (диапазоны $10–30k, годы, пробеги).
- Панель фильтров в ленте (`.feed-panel`, кнопка «Фильтры» со счётчиком
  активных): марка, модель, год от/до, **цена $ от/до**, пробег до, источник
  (encar/kcar). Цены в $ конвертируются в воны запроса (`usdToKrw`) — раньше
  `price_max=20000` фильтровал вопы как доллары и молча отдавал пустоту.
- Сохранённые фильтры в чипсах теперь несут все критерии (было 3).
- Спеки (топливо/КПП/кузов/привод) в `listings` не хранятся (этап 5) —
  в ленте не фильтруются, только в сохранённых фильтрах на парсинге.
- **68 vitest (было 62), tsc чисто.**

## 46. Гостевой гейт избранного + модалка входа (2026-10-17)
- `ui/LoginPrompt.tsx`: провайдер + `useLoginPrompt().show(reason)` — модалка
  «🔒 Нужен вход» (кнопки «Войти» → `/login`, «Позже»), стили `.modal__*`.
- `FavoritesScreen`: гостю раздел закрыт — экран-гейт `.state` («Нужен вход» +
  кнопка «Войти»), модалка показывается сразу, API `/favorites` не дёргается.
- `ListingCard`: клик гостя по ❤ показывает модалку вместо API-запроса.
- Провайдер смонтирован в `MiniApp` внутри `BrowserRouter` (нужен `useNavigate`).
- Проверено Playwright на стенде: гейт, авто-модалка, «Позже», ❤ → модалка,
  «Войти» → `/login`. Скриншоты `shots/guest_*.png`.
- **Стенд**: `gateway` возвращался при каждом `up` (он в `depends_on` miniapp) —
  в `docker-compose.test.yml` `depends_on: !override` только на backend;
  контейнер остановлен и удалён, nginx перезапущен (кэш DNS).
- **72 vitest (было 68; +4: гейт гостя, ❤ гостя/юзера, список избранного),
  tsc чисто.**

## 47. E-commerce аудит вёрстки: сортировка, CTA, sticky-бар (2026-10-17)
- Аудит по PLP/PDP-стандартам (`shots/audit_ecom.py`, мобайл 390 / ПК 1440):
  галерея/цены/бейджи/фильтры/пагинация/пустые состояния — были ок; не хватало
  сортировки, иерархии CTA и sticky-кнопки.
- **Сортировка ленты** (PLP): бэк `sort` = new | price_asc | price_desc |
  mileage_asc | year_desc (`ORDER BY` с `nulls_last` — объявления без цены
  внизу); цены нормализуются в $ через `CASE currency='KRW' THEN price/1350` —
  иначе 4 USD-объявления ломали порядок. Селект `.feed-sort` в саббаре ленты,
  счётчик «207 авто · дешёвые сверху» отражает выбор.
- **Иерархия CTA на PDP**: primary один — «Открыть у источника» (`filled`),
  калькулятор/избранное — `outline` (сердце подсвечивается акцентом),
  «Назад» — `plain`.
- **Sticky CTA-бар на мобиле** (`.listing-cta-bar`): цена + «Открыть у
  источника» над таббаром (`bottom: 64px + safe-area`), на ПК скрыт;
  `padding-bottom` листинга зарезервирован, чтобы «Назад» не перекрывался.
- Спеки PDP дополнены «Обновлено» (дата `parsed_at`).
- Тесты: `Feed.test.tsx` (смена sort → `sort=price_asc&offset=0`), бэк
  `test_list_sort_param` (валидный/невалидный ключ).
- Проверено на стенде: sort=price_asc начинается с ₩5 600 000 (≈$4.1k),
  price_desc — ₩149.9M; sticky-бар только на мобиле, overflow нет.
- **73 vitest, 560 pytest, tsc чисто.**

## 48. UI/UX-полировка: возврат в ленту, навигация, доступность (2026-10-17)
- **Возврат из карточки с сохранением ленты** (главная боль каталога): кэш
  `feedCache` (фильтры, сортировка, items, offset, scrollY) пишется на unmount,
  восстанавливается только на POP-навигации («Назад») — без повторного запроса
  к API; скролл возвращается после отрисовки карточек. `resetFeedCache()` для
  тестов (beforeEach в `Feed.test.tsx`).
- **Кнопка «Назад» поверх галереи** на PDP (`.listing-back`, круглая, blur) —
  раньше выход был только кнопкой внизу страницы.
- **Кнопка «Наверх»** в ленте (`.to-top`) после 1600px прокрутки, плавный
  скролл, на мобиле над таббаром.
- **❤ в sticky-CTA-баре** PDP: цена + избранное + «Открыть у источника» —
  всё в зоне пальца; тап-таргеты 44px.
- **`:focus-visible`** глобально (акцентная обводка) — навигация с клавиатуры.
- Проверено Playwright: лента после «Назад» — 20 карточек, та же якорная
  карточка, scrollY=2400 восстановлен; «Наверх» → 0; полный аудит 9 экранов ×
  2 вьюпорта — ALL CLEAN, JS-ошибок нет.
- **74 vitest (+1: восстановление ленты без повторного запроса), tsc чисто.**

## 52. Цены в ленте — только валюта региона (2026-10-17)
- **Жалоба:** «кнопка цен в профиле вообще никак не влияет на выдачу — как было
  $ и KRW, так и осталось». Диагностика на стенде: переключатель региона работал,
  но менял лишь мелкую вторичную цену в строке «$15 185 · 1 397 000 ₽», а главная
  цена ($ и KRW источника) оставалась неизменной — эффект незаметен.
- **Решение (выбор пользователя):** в ленте и избранном главная цена — только
  валюта региона (₽/Br). $ и цена источника (KRW) из карточек ленты убраны —
  полная цепочка «$ · локальная · источник» остаётся на PDP.
- **ListingCard:** рендер `chain.local ?? chain.original` одной строкой; классы
  `.listing-card__local`/`.listing-card__orig` удалены (CSS + разметка).
- **Favorites:** цены (текущая и «было») тоже конвертируются в валюту региона
  вместо сырых KRW.
- **Profile:** подсказка региона переписана — «В ленте цены — в валюте региона;
  $ и цена источника остаются в карточке объявления».
- **73 vitest, tsc чисто.** Playwright: ru → «1 397 000 ₽», by → «46 300 Br»,
  KRW/$ в карточках ленты отсутствуют, на PDP цепочка полная; JS-ошибок нет.

## 51. Ремонт вёрстки: тени/границы, PDP и Favorites без telegram-ui, контраст кнопок, AI-колонка (2026-10-17)
- **Жалоба:** внутри карточек нет затенений, блок описания квадратный, AI-функционал
  рендерится слева, кнопки не контрастные, вёрстка «поехала».
- **Причины:** тени-токены были почти невидимы (shadow-sm = 0.05 alpha); PDP и
  Favorites всё ещё на telegram-ui Section/Cell/Placeholder — хэшированные классы
  `tgui-*` не матчатся селектором `.Section`, блоки рендерились квадратными без
  рамок; outline-кнопки имели границу цвета `--bg-secondary` (#f4f4f5) на белом —
  невидимую.
- **Токены:** тени усилены до двухслойных (16,24,40); новые `--border`
  (tg section-separator-color, адаптируется тёмной темой) и `--border-strong`;
  все 14 рамок `var(--bg-secondary)` → `var(--border)`; `.panel`, карточки —
  рамка + тень.
- **Listing (PDP):** telegram-ui удалён полностью — характеристики теперь карточка
  `.panel.spec-card` (заголовок, крупная цена `.spec-card__price`, строки
  `.info-row`), «Название с источника» и «История цен» — панели с `.info-row`,
  AI-блок — `.panel` с заголовком, состояния — `.state`, CTA — `.btn`/`.btn--primary
  .btn--block` с иконками lucide.
- **Favorites:** список — `.panel` + строки `.filter-item` (клик → карточка,
  ✕ `.btn--danger`), состояния — `.state` (+ кнопки «Войти»/«К ленте»),
  загрузка — `.skeleton`. telegram-ui удалён.
- **Кнопки:** `.btn` — граница `--border-strong` + font-weight 600 + hover;
  telegram-ui Button заменён на `.btn` в AiSearch («Запустить поиск», «Отправить»,
  «Сброс», ссылка `.ai-chat__link`), Calc, Compare, Onboarding (loading →
  текст «Считаю…»/«Сравниваю…»/«Запускаю…»).
- **AI-чат:** колонка сужена 760 → 640px (по центру контентной области), интро —
  полноценная карточка (рамка + тень md).
- **73 vitest, tsc чисто.** Playwright: тени/рамки/радиусы/контраст подтверждены
  computed-стилями на стенде; telegram-ui секций на PDP — 0; полный аудит
  9 экранов × 2 вьюпорта — ALL CLEAN, JS-ошибок нет.

## 50. Пагинация «как в Instagram» + липкая боковая панель (2026-10-17)
- **Лента:** кнопка «Показать ещё» убрана — лента сама догружает страницы
  при приближении к концу (rootMargin 800px у сентинела + фолбэк-триггер
  «близко к низу документа» по скроллу). Подгрузка не спамит API: busy/error
  ref-флаги, после конца списка запросов нет; при ошибке — плашка «Повторить».
  Кэш ленты (возврат по «Назад») работает как раньше — восстанавливает всё
  загруженное и позицию скролла.
- **Десктоп:** боковая панель с вкладками теперь `position: sticky; top: 0;
  height: 100vh` — остаётся на экране при любом скролле ленты (раньше уезжала
  вверх вместе со страницей). Мобильный таббар и так фиксирован.
- Тесты пагинации переведены на триггер `scroll` (jsdom не умеет ни IO, ни
  скролл): авто-догрузка до конца списка, отсутствие запросов после конца,
  ретрай после ошибки.
- Проверено Playwright на стенде: ПК — 20 → 80 карточек за 6 прокруток,
  сайдбар top=0/sticky после глубокого скролла; мобайл — 20 → 60, таббар
  прижат к низу; полный аудит 9 экранов × 2 вьюпорта — ALL CLEAN.
- **73 vitest, tsc чисто.**


## 49. Единый дизайн на всех экранах: Login, Filters, Profile (2026-10-17)
- Проблема: дизайн-система была только на основных экранах (лента, карточка,
  калькулятор, сравнение, AI-чат), а Login (0 классов), Filters и Profile
  оставались на дефолтных Section/Cell/Input/Placeholder telegram-ui с inline-
  стилями — визуально «чужие» страницы.
- **Login**: свой хиро (🔐 + заголовок), сегмент-табы «Вход/Регистрация»
  (`.auth__tabs`), поля `.calc-field`, кнопка `.btn--primary .btn--block`,
  форма на `<form onSubmit>` (Enter = отправка), ошибки `.form-error`.
- **Filters**: обе секции — карточки `.panel`; все поля `.calc-field`/`.filter-select`
  в парах `.filter-pair`; Instant-свитч → тумблер-чип `.feed-toggle`; список
  фильтров — строки `.filter-item` с описанием критериев и ✕ `.btn--danger`;
  пустое состояние и загрузка — `.state`/`.skeleton`.
- **Profile**: карточки `.profile-card` (гостевой режим с CTA-ссылкой-кнопкой,
  регион с чипами, профиль, подписка); строки «label → значение» `.info-row`;
  тариф — акцентный блок `.sub-tier` + лимиты + кнопки покупки `.btn`;
  «Выйти» — `.btn--danger .btn--block`; авторизация/загрузка — `.state`.
- telegram-ui на этих экранах больше не используется (нули импортов).
- Новые стили: `.btn--block`, `.auth__*`, `.filters-form*`, `.filter-item*`,
  `.info-row*`, `.sub-*`, `.profile-card*`.
- Проверено Playwright: `/login`, `/filters`, `/profile` × mob/PC — без
  overflow-x, без JS-ошибок, табы логина переключаются; полный аудит 9 экранов —
  ALL CLEAN.




## 53. Вход через Telegram с сайта: ссылка на бота из env (2026-10-17)
- Флоу: кнопка «Войти через Telegram» на Login → POST /api/auth/telegram/link
  (одноразовый код, Redis tglogin:{code}, TTL 600 с) → открытие
  https://t.me/{BOT_USERNAME}?start=login_{code} → пользователь жмёт Start →
  бот зовёт internal POST /api/auth/telegram/link/confirm (профиль из
  from_user, статус ok, TTL 120 с) → фронт опрашивает каждые 2,5 с (до 3 мин)
  GET /api/auth/telegram/link/{code} и получает JWT-пару + upsert пользователя
  (source=telegram_link). Код одноразовый, без токена confirm отдаёт 401.
- **Ссылка на бота строится из env**: новая переменная BOT_USERNAME (без @)
  в Settings (.env/.env.example); пусто → 503, фронт показывает «вход через
  Telegram недоступен». Текущее значение: ftadasd_bot (получено getMe).
- Bot: BackendClient.confirm_login_link + handle_deep_link ветка login_
  («✅ Вход подтверждён…» / «⌛ Ссылка устарела»). Bot-хендлер смок-тестить на
  стенде нельзя (поллинг токена в проде) — юнит-тесты в test_bot_calc.py.
- Frontend: createTelegramLoginLink/pollTelegramLogin в api/auth.ts (токены
  пишет setTokens), Login.tsx — кнопка с иконкой Send, разделитель «или»,
  карточка ожидания `.tg-login` («Открыть бота снова» / «Отменить»),
  автосброс по expired/таймауту, cleanup при размонтировании. Стили:
  `.auth__divider`, `.tg-login*` (границы var(--border), обе темы).
- Проверено на стенде curl-циклом: link → pending → confirm ok → JWT-пара →
  повторный GET expired. Образы miniapp/backend пересобраны
  (-p auto_bot_test, реcтарт miniapp после force-recreate, .Image совпадает).
- **567 pytest (+6), 76 vitest (+3), ruff чисто.** Прод-стек (backend+bot)
  redeployить отдельно — иначе прод-бот не подтвердит login_-коды.
## 54. Лента: убран сырой корейский title_raw с карточек (2026-10-17)
- Жалоба «надписи на карточках кривые»: замеры Playwright (overflow/wrap/clip)
  чистые — проблема была в контенте. На стенде у всех 207 listings title_ru
  пуст (translate-сервис не запущен), поэтому второй строкой карточка показывала
  сырой корейский title_raw («현대 더 뉴 그랜저 가솔린…») — визуальный мусор.
- **ListingCard**: .listing-card__sub рендерится только при наличии title_ru
  (переведённый тайтл). Без перевода — одна строка тайтла; title_raw остаётся
  на PDP (там он уместен как «название у источника»). Проверено на стенде:
  sub=null на всех карточках, переносов/обрезаний нет.
- Корейская модель в самом тайтле («Hyundai 더 뉴 그랜저») — данные kcar
  (modelNm с источника), обратного KR→EN словаря нет; лечится переводом
  (этап 5, services/translate + бэкфилл title_ru).
- **78 vitest (+2: sub без title_ru скрыт; с title_ru — перевод).**
## 55. Кнопки «назад» на экранах расчётов (2026-10-17)
- Проблема: «Доставка и растаможка» с карточки ведёт на /calc (и дальше
  «Сравнить» → /compare), но на этих экранах не было возврата — только таббар,
  который теряет контекст объявления.
- Новый ui/BackButton (круглая «‹» по токенам: --border/--bg-secondary,
  обе темы) в sticky-заголовке Calc и Compare (`.feed-header--with-back`),
  navigate(-1) → возврат на PDP.
- Проверено Playwright на стенде: PDP → CTA → /calc (кнопка есть) → назад
  вернулись на /listing/1; /compare → назад → лента. Скриншот back_calc.png.
- **80 vitest (+2: BackButton в Calc и Compare реально делает navigate(-1)).**
## 56. AI-анализ цены закрыт для гостей (2026-10-17)
- Требование: аналитика — только авторизированным. Раньше гость мог 1 раз/нед.
- **Backend** (POST /listings/{id}/analysis): гость = telegram_id < 0 И email
  пуст (email-регистрация тоже с id < 0, но с email; TG-юзеры с реальными id).
  403 guest_forbidden до вызова LLM — денег не тратим. Маркер — 
  _is_web_guest() в analysis.py (SimpleNamespace-юзеры тестов совместимы).
- **Frontend** (Listing): GET /analysis гость не запрашивает вовсе; клик по
  «Анализировать» → модалка входа (LoginPrompt). Кнопка для гостя —
  «🤖 Анализировать — по входу», подсказка с 🔒.
- Тексты синхронизированы: гест-баннер App, карточка гостя Profile,
  докстринги /auth/guest и guestLogin.
- Проверено на стенде: гостевой JWT → POST 403 guest_forbidden; Playwright:
  гость видит «по входу», клик открывает модалку входа, кэша разбора нет.
- **569 pytest (+2: гость 403 без LLM; email-юзер с id < 0 проходит).**
## 57. Контраст в карточке товара (PDP) — тёмная тема чинила белый текст на белом (2026-10-17)
- **Корень:** стили использовали var(--tgui--*) telegram-ui, но либа задаёт
  их только внутри своего appearance-класса; вне его всегда светят фолбэки
  (#fff фон, #222 текст). В тёмном Telegram: CTA-бар белый + цена наследует
  белый --text → «не видно». Заменены все 27 вхождений на свои токены
  (--bg/--text/--bg-secondary/--border/--border-strong/--link).
- **AppRoot appearance:** tgAppearance() (Telegram colorScheme) передаётся во
  все 4 AppRoot — Tabbar и компоненты либы следуют теме.
- **Таббар:** .app-tabbar переопределяет --tgui--secondary_hint_color →
  --text-secondary (2.32:1 → 4.7:1 светлая / 6.9:1 тёмная).
- **Галерея:** чип «⤢» 0.45 → 0.75 black (≥3:1 на любом фото).
- **Фолбэк акцента** (браузер без TG): #2481cc → #1e73b8 (4.13 → 5.0:1
  с белым). В Telegram остаётся парный цвет темы.
- **Аудит:** shots/contrast_audit.py — WCAG-контраст всех текстов/кнопок PDP
  в обеих темах; к финалу в light только иконка ✕ 4.24 (≥3:1 для графики).
- 80 vitest, tsc — чисто; деплой miniapp, гостевой флоу п.56 не задет.
## 58. Контраст ленты и Calc + юрисдикция растаможки по валюте (2026-10-17)
- **Жалоба** «белое на белом в ленте и на растаможке» на стенде не
  воспроизводится: shots/contrast_audit_feed.py (лента) и новый
  contrast_audit_calc.py (/calc, обе темы) чистые — после фиксов п.57
  (токены вместо --tgui--*) осталось только 3.72:1 на активных чипах/кнопке —
  артефакт эмуляции (браузерный шим без парного акцента TG; в клиенте
  акцент парный теме). Аудиты в shots/ для повторных прогонов.
- **Юрисдикция по валюте объявления:** BYN → расчёт привоза по РБ (Маршрут
  Корея → Владивосток → Минск, сбор за оформление и утильсбор РБ, итог в BYN),
  RUB → РФ (как было). Пошлины ЕАЭС (Решение Совета ЕЭК № 54) общие.
- **Backend** (delivery.py): параметр region="ru"|"by" в calc_delivery;
  LOGISTICS_BY_USD=3600 (море+перегон до Минска), BROKER_BY_USD=150,
  LAB_BY_USD=250 (СБКТС/ЭПТС РБ), сбор оформления РБ 48 BYN (ориентир),
  утильсбор РБ 544,5 BYN × коэф. (льготный 0.5 при ≤3000 см³ ≈ 272 BYN,
  полный ×2.0); LEGAL_REFS_BY со ссылками на НПА РБ + «проверять актуальную
  редакцию» и customs.gov.by. В компонентах появилось поле local (местная
  валюта), итоги total_local + currency («₽»/«Br»); total_rub для РФ = local
  (совместимость бота/агента). API /calc/delivery: region в in/out;
  /calc/compare: region прокидывается per-item.
- **Frontend:** fx.ts — toUsd теперь умеет RUB ÷92 и BYN ÷3.05 (раньше
  объявление в ₽/Br считалось как USD — фикс бага цены!), regionFromCurrency();
  Listing CTA добавляет &cur= в deep-link; Calc — чипы 🇷🇺/🇧🇾 (выбор
  запоминается setRegion), region из ?cur=, итог/строки в местной валюте,
  route-заметки и дисклеймер по региону.
- Ставки РБ — честные «ориентиры MVP» (в UI и legal_refs помечены
  «проверять»): точные редакции постановлений Совмина РБ сверять перед
  продом.
- **575 pytest (+6: BY-расчёт, НПА РБ, полный утильсбор >3000 см³, дефолт ru,
  422 region=kz, compare-прокидка), 83 vitest (+3: RUB/BYN в toUsd,
  переключатель РБ шлёт region=by и рисует Br, deep-link ?cur=BYN), ruff/tsc
  чисто.** Деплой miniapp+backend на тестовый стенд; /api/calc/delivery
  region=by отвечает Br живьём.
## 59. Ховер кнопок + Compare по региону + redeploy прода (2026-10-17)
- **Ховер (жалоба «на кнопках при ховере хуёво видно»):** корень —
  `.btn:hover:not(:disabled) { background: var(--bg-secondary) }` перебивал
  `.btn--primary` (специфичность 0,3,0 против 0,1,0): при наведении primary
  теряла акцентный фон, оставая белый текст на светло-сером. Фикс: hover
  смены фона только для не-primary (`:not(.btn--primary)`), для primary —
  `filter: brightness(0.88)` (затемнение, контраст accent-text только растёт,
  обе темы). Новый shots/contrast_hover.py — WCAG-контраст кнопок под ховером
  в light/dark.
- **Compare по юрисдикции** (хвост п.58): чипы 🇷🇺/🇧🇾 (как в Calc, выбор
  синхронизируется через setRegion), region в каждом item запроса, итоги в
  местной валюте (`total_local` + currency, $ вторым числом), route-заметка
  по региону. CompareItem: total_local/currency. **85 vitest (+2: чип РБ →
  region=by и Br в итоге; дефолт ru).**
- **Грабли стендов:** поднимал тестовый проект (`-p auto_bot_test`) без
  оверрайда `deploy/docker-compose.test.yml` → контейнеры пересоздались на
  боевых портах 8080/8443. Восстановлено правильной командой (тест: 8081/8444,
  LLM mock). Запомнить: тест-стенд поднимать только через
  `docker compose -p auto_bot_test -f docker-compose.yml -f
  deploy/docker-compose.test.yml --profile mono up -d ...` (или teststand.sh).
- **Redeploy прода** (п.4 бэклога): боевой стек (default project, prod
  override) пересобран и поднят — backend+bot с новым кодом (login_-коды и
  регион-расчёт в проде), п.56–58 в проде.


## 60. Фильтры и карточки как на avtokorea.by + обогащение encar detail (2026-10-17)

**Референс:** фильтры avtokorea.by/search (марка→модель→поколение, топливо,
объём от/до, КПП, цвет, год/цена/пробег от-до) и карточки (цена $ + BYN,
спек-строка «топливо · объём · КПП»). Осмотры (карта повреждений + чек-лист)
у них — ручные WP-репорты; решили тянуть только из encar, показывать только
в вебаппе.

- **Карточка (ListingCard):** спек-строка `⛽ топливо · 🔋 2.0 · ⚙️ автомат ·
  🚙 кузов · 🔧 привод · 🎨 цвет` (labelOf — канон + сырое значение источника
  как есть); мета — «N км · 🇰🇷 Корея · дата»; ссылка на источник (encar.com ↗
  / kcar.com ↗, stopPropagation — не открывает PDP).
- **Лента (Feed):** в панель фильтров добавлены кузов/топливо/КПП/привод
  (API 0016 уже принимал — фронт не передавал), пробег «от», объём от/до (л),
  цвет. Марка/модель — зависимые селекты из нового `GET /api/listings/facets`
  (DISTINCT make / model WHERE make, как есть в БД). FeedQuery расширен,
  deep-link + sameQuery учат новые поля. Справочники вынесены в lib/specs.ts
  (Feed/Filters без дублей), COLORS — 16 цветов (канон англ., подписи рус.).
- **API:** `mileage_min`, `engine_volume_min/max` (ge=0/le=20), `color`
  (ilike), `/facets` (до `/{listing_id}` — порядок роутов).
- **Обогащение encar detail (миграция 0017: listings.engine_volume Float,
  listings.color Text):** `api.encar.com/v1/readside/vehicle/{source_id}`
  отдаёт spec: bodyName/displacement(см³)/colorName(кор.)/fuelName/
  transmissionName — всего, чего нет в list-API. `enrich_listing_specs()`
  (services/fetch.py): только encar, только если поля NULL (повторные PDP
  бесплатны), normalize_body/fuel/transmission + новые normalize_color
  (COLOR_MAP кор/рус/англ) и normalize_engine_volume (см³→л, 1 знак).
  Подключено лениво в GET /api/listings/{id}; сбой encar не ломает PDP.
- **Осмотры (карта повреждений/чек-лист):** прямой inspection-эндпоинт encar
  не найден (перебор /v1/readside/... 404, fem-SPA без данных в HTML);
  condition.inspection.formats=["TABLE"] — осмотр рендерится на детальной
  странице, данные надо копать дальше (БД-чёрный список из detail API пока
  только спеки). Перенесено в бэклог.
- **Счётчики: 598 pytest (+9: color/volume normalize, enrichment ×3, API
  mileage_min/facets×2/volume+color), 85 vitest.**
- **Деплой:** прод (коммит 0d493d0): build+up backend/miniapp, alembic 0017.
  Живая проверка: лот 231 (encar 42935199) → sedan/diesel/automatic/2.0/blue;
  `?engine_volume_min=2.0&engine_volume_max=2.0` → total=1. Тест-стенд —
  то же, миграция прошла.

## 61. Осмотр: карта повреждений + чек-лист на PDP (миграция 0018) (2026-10-17)

- **Исследование encar (доделано):** в бандле fem.encar.com найдены реальные
  пути — `readside/diagnosis/vehicle/{id}`, `readside/inspection/vehicle/{id}`,
  `readside/record/vehicle/{id}/open?vehicleNo=...` (базовый URL пустой =
  same-origin, fem отдаёт SPA-fallback; api.encar.io мёртв). Все отдают 404 —
  диагностика encar (`view.encarDiagnosis`) закрыта авторизацией (withAllToken,
  гостевого токена в бандле нет). Вывод: structured-парсинг заблокирован.
- **Решение — свои отчёты осмотра (как у avtokorea.by, где осмотры тоже
  ручные):** таблица `inspection_reports` (миграция 0018, применена на стенд):
  listing_id unique FK, source (manual|encar — слот под будущий парсинг),
  inspector, inspected_at, grade (excellent/good/fair/poor), summary,
  marks JSONB [{zone,type,note}], items JSONB [{key,status,note}].
- **Справочники** в `app/schemas/listing.py`: 14 зон кузова (BODY_ZONES),
  7 типов дефектов (MARK_TYPES), 12 агрегатов чек-листа (ITEM_KEYS),
  4 статуса (ok/warn/defect/na). Ключи синхронизированы с фронтовым
  `frontend/src/lib/inspection.ts` (там же русские лейблы, цвета, severity).
- **API** (app/api/listings.py): `GET /api/listings/{id}/inspection` (404 если
  нет — фронт молча скрывает блок), `PUT` — только role=admin (upsert).
  ⚠️ Искали по PK session.get → фикс: select по listing_id (PK у таблицы id).
- **UI** (frontend/src/components/Inspection.tsx): SVG-схема кузова вид
  сверху (14 зон), зоны с дефектами залиты цветом по типу (replace > crack >
  dent > rust > repaint > glass > scratch), тап по зоне — детали; легенда;
  чек-лист с ✅⚠️❌➖ и заметками; бейдж грейда; summary. Секция «🔍 Осмотр»
  на PDP (Listing.tsx) после блока анализа. CSS — `.inspection__*` в index.css.
- **Демо:** отчёт осмотра вставлен для лота 231 (проверено GET 200 в API).
- **Счётчики: 602 pytest (+4 inspection), 88 vitest (+3 InspectionSection),
  tsc/ruff чисто.**
- **Дальше:** деплой прода (build miniapp + backend, миграция 0018); по
  желанию — форма ручного ввода осмотра для админа (сейчас только PUT API).

## 2026-10-17: фикс «Ссылка входа устарела» (site login через бота)

- **Симптом:** вход на сайте через Telegram-бота падал с «⌛ Ссылка входа
  устарела — запросите новую на сайте»; вход в Mini App (initData) работал.
- **Причина:** одновременно были подняты СЕРВИСЫ ОБОИХ профилей compose —
  mono `backend` (:8000) и micro `gateway`. У обоих сетевой алиас `backend`,
  Docker DNS отвечал двумя IP (round-robin): сайт создавал `tglogin:{code}`
  в Redis на одном инстансе, а confirm от бота прилетал на другой (там ключа
  нет → 404 → бот отвечает «устарела»). Плюс у gateway REDIS_URL был
  `redis://localhost:6379/0` (из .env, без compose-оверрайда) — внутри
  контейнера localhost это он сам, Redis'а там нет.
- **Фикс:** `docker compose stop gateway` (микросервисы и mono не должны
  работать одновременно — это взаимоисключающие профили), restart bot +
  miniapp (сброс DNS-кэша nginx/контейнера).
- **Проверено E2E:** link → confirm (200 ok, X-Internal-Token) →
  GET status = JWT-пара + user → повторный GET = expired (одноразовость).
  Тестовый юзер из БД удалён.
- **Правило на будущее:** поднимать строго один профиль
  (`--profile mono` ИЛИ `--profile micro`); если нужен micro — погасить mono
  и прописать микросервисам REDIS_URL через compose environment.
