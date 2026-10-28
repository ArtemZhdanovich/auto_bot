# Тесты

Как устроены тесты, как запускать, флейки, контракты моков, как добавлять
новое. Свод правил: транспорт тонкий — логику гоняем на сценариях
(application) с фейками; SQL-зависимое — сквозные через ASGI-клиент на
реальном Postgres.

## 1. Запуск

```bash
# --- бэкенд (pytest ~1470 тестов, ~7 мин) -------------------------------
uv run pytest -q                              # всё
uv run pytest tests/catalog -q                # контур каталога/рынка/чатов
uv run pytest tests/infrastructure -q         # воркеры, S3, источники, очередь
uv run pytest tests/dialog/test_rag.py -q     # один файл (изоляция флейка)
uv run pytest tests/catalog/test_market.py::test_name -q   # один тест
uv run pytest -q -p no:cacheprovider          # как в CI (без .pytest_cache)
uv run pytest -q -x                           # стоп на первом падении (отладка)
uv run pytest -q -k "market and not photos"   # выбор по подстроке имени

# --- фронтенд (vitest, 552 теста / 44 файла) ----------------------------
# ВАЖНО: только с явным threads-пулом, иначе OOM/RangeError (tinypool
# плодит воркеров по числу ядер; см. §4):
cd frontend && npx vitest run --pool=threads \
  --poolOptions.threads.minThreads=1 --poolOptions.threads.maxThreads=2

# один файл (те же флаги):
cd frontend && npx vitest run src/screens/Feed.test.tsx --pool=threads \
  --poolOptions.threads.minThreads=1 --poolOptions.threads.maxThreads=2

# режим наблюдения при разработке:
cd frontend && npx vitest --pool=threads \
  --poolOptions.threads.minThreads=1 --poolOptions.threads.maxThreads=2

# --- статика/типы/линт ---------------------------------------------------
cd frontend && npm run build                  # tsc -b + vite build (типы)
uv run ruff check app tests                   # линт (как в CI)
```

Postgres для pytest: тесты сессии берут `TEST_DATABASE_URL`, иначе
`<DATABASE_URL>_test` (autoselect_test); схема пересоздаётся на старте
сессии. БД недоступна → зависимые тесты **скипаются**, а не падают. Живой
Redis не нужен: API-тесты всегда получают `FakeRedis`.

## 2. Структура бэкенда (tests/**)

Конфигурация: [pyproject.toml](../pyproject.toml) (`asyncio_mode=auto`,
`testpaths=tests`, `pythonpath=.` — импорт `services.*`). Общие фикстуры —
[tests/conftest.py](../tests/conftest.py) (см. §5).

| Каталог | Файлы | Что покрывает |
|---|---|---|
| `tests/admin/` | `test_admin_api.py` — CRUD источников/адаптеров/лидов, SSRF-валидация конфига; `test_admin_audit.py` — аудит-логи без секретов; `test_admin_assistant.py` — LLM-ассистент; `test_analytics.py`, `test_funnels.py` — аналитика/retention; `test_metrics.py` — `/metrics` (кэш, 503 без БД); `test_monitoring.py` — алерты/дедуп; `test_parser_doctor.py` — ИИ-ремонт селекторов; `test_registry.py` — реестр источников | админ-контур (X-Internal-Token) |
| `tests/application/` | `test_chat_privacy.py` — маскировка контактов; `test_ingest.py` — ingest объявлений (дедуп/цены); `test_quota_use_cases.py` — квоты тарифов | сценарии без транспорта |
| `tests/architecture/` | `test_layers.py` — Clean Architecture-слои (transport не импортирует infrastructure напрямую и т.п.); `test_services_smoke.py` — смоук всех сервисов профиля micro | архитектурные инварианты |
| `tests/billing/` | `test_payments_api.py` — Stars: pending→paid, идемпотентность ретрая вебхука, kind subscription/placement | платежи |
| `tests/bot/` | `test_bot_analysis.py`, `test_bot_backend_client.py`, `test_bot_calc.py`, `test_bot_dialog_ui.py`, `test_bot_inline_share.py`, `test_bot_payments.py`, `test_bot_throttle.py` — хендлеры, клиент бэка, инлайн-шеринг, pre_checkout, троттлинг | Telegram-бот |
| `tests/catalog/` | `test_listings.py`, `test_listings_keyset.py` — лента/keyset-пагинация; `test_analysis_api.py`, `test_price_analysis.py`, `test_similar.py`, `test_history_report.py` — AI-анализ/похожие/отчёт истории; `test_favorites*.py` — избранное (роутер и use case); `test_criteria_limits.py`, `test_filters…` — фильтры; `test_market.py`, `test_market_lots.py`, `test_market_feed.py`, `test_market_photos.py`, `test_market_description.py` — внутренний рынок; `test_listing_lifecycle.py`, `test_listing_quality.py` — жизненный цикл/качество; `test_translate.py` — перевод тайтлов; `test_deals_agent_thread.py`, `test_deals_contact.py` — сделки; `test_chat_bot.py`, `test_chats.py`, `test_chat_groups.py`, `test_chat_governance.py`, `test_chat_operator.py`, `test_chat_personal_channel.py`, `test_chat_summary.py` — чаты всех видов | самый большой контур |
| `tests/di/` | `test_container.py` — сборка Dishka-контейнера, провайдеры | DI |
| `tests/dialog/` | `test_dialog_api.py`, `test_dialog_store.py` — API и Redis-стор; `test_agent.py`, `test_tools.py` — агентский цикл/тулзы; `test_ai_search.py` — AI-поиск; `test_rag.py` — RAG-клиент + интеграция answer_question (⚠ флейк, §4) | AI-диалог |
| `tests/domain/` | `test_favorites_use_cases.py` — доменные сценарии избранного | домен |
| `tests/identity/` | `test_auth_api.py` — register/login/refresh; `test_auth_guest.py` — гостевые сессии; `test_security_initdata.py` — HMAC initData (экспир, дубли ключей, будущее); `test_me.py`, `test_onboarding.py` | аутентификация |
| `tests/infrastructure/` | `test_fetch.py`, `test_send_worker.py` — воркеры (cron'ы, дайджесты, экспирация размещений); `test_cron.py`, `test_scheduler.py` — расписание; `test_encar.py`, `test_encar_adapter.py`, `test_kcar.py`, `test_kcar_adapter.py`, `test_che168.py`, `test_che168_adapter.py` — парсеры; `test_dedup.py`, `test_normalize.py` — дедуп/нормализация; `test_delivery.py`, `test_notify.py` — доставка уведомлений, лимиты/тихие часы; `test_s3_mirror.py`, `test_storage.py` — S3/зеркало; `test_http_client.py`, `test_proxy.py` — HTTP/прокси DataImpulse; `test_llm_client.py` — LLM-клиент; `test_trace.py`, `test_heartbeat.py` — трейсинг/liveness; `test_favorites_repository.py` — репозиторий | инфраструктура |
| `tests/messaging/` | `test_topology.py` — exchange/очереди/DLX; `test_publisher.py`, `test_enqueue_many.py` — публикация/батчи; `test_relay.py` — faststream-relay | очередь |
| `tests/partners/` | `test_partners.py` — CRUD/ключи/ротация; `test_leads.py` — пул/claim-инварианты; `test_partner_billing.py` — CPL-баланс; `test_partner_owner_market.py` — owner-рынок партнёра | партнёрский контур |
| `tests/security/` | `test_prod_secrets.py` — fail-fast секретов в prod; `test_internal_token_empty_rejected.py` — internal-API без токена; `test_rate_limit.py` — token bucket/партнёрский лимит; `test_guest_rate_limit.py` — гостевой IP-лимит; `test_pq_crypto.py` — ML-KEM-шифрование | безопасность |
| `tests/transport/` | `test_gateway_routing.py`, `test_gateway_proxy.py` — таблица маршрутов/прокси gateway; `test_miniapp_api.py` — контракты миниаппа (схемы без утечек ORM-полей); `test_calc_api.py` — калькулятор; `test_notifications_api.py` — настройки уведомлений; `test_enrich_handler.py` — enrich-хендлер | HTTP-транспорт |

## 3. Структура фронтенда (frontend/src/**/*.test.tsx)

Config: [frontend/vitest.config.ts](../frontend/vitest.config.ts) — jsdom,
`globals: false`. Рядом с кодом (colocation). 44 файла / 552 теста.

| Файл | Что покрывает |
|---|---|
| `App.guest.test.tsx` | гостевой режим сайта: автологин гостя вне Telegram, демо-баннер, апгрейд через /login, keep-alive табов |
| `api/auth.test.ts`, `api/client.test.ts` | API-клиент: Bearer/refresh-ретраи, преобразование ошибок |
| `components/Inspection.test.tsx` | чек осмотра: диаграммы повреждений, чек-лист |
| `components/ListingCard.test.tsx` | карточка ленты: цены $/₽, бейджи, фото |
| `components/Shell.test.tsx`, `components/Tooltip.test.tsx` | навигационная оболочка, тултипы |
| `components/chat/ChatList.test.tsx`, `components/chat/ChatRoom.test.tsx` | список чатов, комната: отправка, typing, поллинг-фоллбэк |
| `lib/analysis.test.ts` | парсинг вердиктов AI-анализа |
| `lib/chatApi.test.ts`, `lib/chatSocket.test.ts`, `lib/chatStore.test.ts` | чат-API, PollingTransport (ws→REST `after_id`), стор чатов |
| `lib/external.test.ts` | внешние ссылки (t.me и пр.) |
| `lib/fx.test.ts` | курсы/конвертация валют |
| `lib/markdown.test.tsx` | markdown → sanitized HTML (DOMPurify) |
| `lib/marketPhotos.test.ts` | uploadMarketPhoto: FormData, refresh-ретрай, клиентские проверки файла |
| `lib/persistentState.test.ts` | персистентное состояние (localStorage) |
| `lib/theme.test.ts`, `lib/viewHistory.test.ts` | тема, история просмотров |
| `partner/components/chat/PartnerChatRoom.test.tsx` | партнёрская комната чата |
| `partner/helpers.test.tsx`, `partner/inspections.test.tsx`, `partner/partner.test.tsx`, `partner/partnerChats.test.tsx` | кабинет: вход по ключу (маска, localStorage, 401-ревокация), навигация/шторка, чеки осмотра, чаты партнёра |
| `screens/AiSearch.test.tsx` | AI-поиск диалогом |
| `screens/Analyses.test.tsx` | история разборов |
| `screens/Calc.test.tsx` | калькулятор «под ключ» (РФ/РБ) |
| `screens/Chats.test.tsx` | экран чатов |
| `screens/Compare.test.tsx` | сравнение |
| `screens/Favorites.test.tsx` | избранное |
| `screens/Feed.test.tsx` | лента: viewport-scroll, IntersectionObserver, скелетоны, «Повторить» |
| `screens/Filters.test.tsx` | фильтры |
| `screens/Landing.test.tsx`, `screens/Login.test.tsx` | лендинг, вход/регистрация |
| `screens/Listing.test.tsx` | PDP V2: двухколоночная структура, contact-CTA, гость → логин-промпт |
| `screens/MyLotsPanel.test.tsx` | «мои объявления» |
| `screens/Profile.test.tsx` | профиль/тариф |
| `screens/SellCar.test.tsx` | визард продажи: шаги, валидация, пропуск фото |
| `screens/apiErrorText.test.ts` | человекочитаемые тексты ошибок API |
| `styles.test.ts` | CSS-токены/инварианты стилей |
| `ui/LoginPrompt.test.tsx`, `ui/Toast.test.tsx` | логин-промпт, тосты |
| `admin/Support.test.tsx` | операторская поддержка: список тредов, паузы, комната, поллинг 30с |

## 4. Флейки и их природа

### 4.1 `tests/dialog/test_rag.py::test_answer_question_without_rag_uses_llm_answer`

- **Симптом:** падает в полном прогоне, изолированно (`uv run pytest
  tests/dialog/test_rag.py -q`) зелёный.
- **Природа:** тест зовёт `run_agent(..., rag=None)` и ожидает LLM-ответ.
  Если в окружении задан `RAG_SERVICE_URL` (например, локальный `.env` со
  `http://localhost:8100`) и RAG включён, `run_agent` создаёт реальный
  клиент (`get_shared_rag_client`) — тул `answer_question` ходит в живой
  RAG. Когда ai-профиль поднят, RAG возвращает свой ответ (confidence ≥
  `RAG_CONFIDENCE_THRESHOLD`) и assert на LLM-текст падает; когда сервиса
  нет — RAG-ошибка → LLM-fallback → тест зелёный. Это же — боевое
  поведение (RAG деградирует в LLM), см. [RUNBOOK.md](RUNBOOK.md) И-9.
- **Обход:** гонять сессию без живого RAG:
  `RAG_SERVICE_URL= RAG_ENABLED=false uv run pytest -q`, либо останавливать
  `rag_api` на время прогона.
- **Не блокер:** интеграция с настоящим сервисом не является контрактом
  unit-прогона.

### 4.2 vitest OOM / RangeError

- **Симптом:** `FATAL ERROR: Reached heap limit` / `RangeError: Array
  buffer allocation failed`, воркеры tinypool умирают пачками.
- **Природа:** дефолтный пул плодит воркеров под все ядра; 44 файла с
  jsdom+React съедают память. Решение — ровно 1–2 threads-воркера
  (обязательные флаги из §1; PROGRESS.md: «threads=2: minThreads=1
  обязателен — tinypool конфликтует с дефолтной min»).
- **Проверка изоляции:** один файл с теми же флагами — если один файл
  падает и с пулом 1, это не OOM, а тест/код.

### 4.3 Прочее известное

- Тесты сессии БД скипаются целиком без Postgres — «внезапно меньше
  тестов» в CI без сервисов это норма (CI гоняет pytest без Postgres).
- Партнёрская модерация сэмплирует 10% в рандом — autouse-фикстура
  `_deterministic_moderation_sampling` фиксирует random вне тестов самого
  сэмплинга (`test_listing_lifecycle` патчит локально).

## 5. Контракты моков (tests/conftest.py)

- **`FakeRedis`** — мини-стаб: get/set/setex/nx/expire/incr/keys/scan_iter/
  pipeline; списки для DialogStore. Подменяет `Redis` в dishka-контейнере
  (`install_test_container`), поэтому API-тесты не требуют живого Redis.
- **`FakeSession`** — стаб AsyncSession: `add()` копит объекты и
  проставляет id/created_at (имитация flush), `execute()` — результат из
  `set_result(s)`/`set_results([...])`, `rowcount=1` для UPDATE
  (mark_paid-тесты), commit/rollback — флаги.
- **`install_test_container(app, session, redis, extra)`** — пересборка
  Dishka-контейнера на фейках: `AsyncSession` → REQUEST, `Redis` → APP;
  `extra`-провайдеры перекрывают любые порты (подмена LLM, RAG, publisher).
  Аутентификация НЕ подменяется: кладёте `User` в сессию и ходите с
  настоящим Bearer (`create_access_token`).
- **`engine`/`session`** — реальный Postgres: схема `drop_all/create_all`
  раз в сессию; тест живёт в savepoint-транзакции
  (`join_transaction_mode=create_savepoint`), commit хендлера не протекает.
- **Фикстуры-пользователи:** `user` (buyer), `free_user`/`pro_user`/
  `business_user`, `guest_user` (telegram_id < 0), `inactive_user`,
  `auth_headers*`/`guest_headers`/`internal_token`; данные: `listing`,
  `lead`, `session_with_analysis`.
- **Query budget:** `assert_max_queries(n)` — контроль N+1 через счётчик
  SQLAlchemy-событий.
- **Моки фронтенда:** jsdom + MS API через перехват `fetch`
  (client.test.ts тестирует retry-логику), `MockLLMClient(script=[...])` —
  сценарный LLM-клиент бэкенда (возвращает очередь LLMResult/ToolCall).

## 6. Как добавить тест

### Новый use case (бэкенд)

1. Сценарий на фейках: инстанцируйте use case с `FakeSession`/`FakeRedis`
   (или своим стабом порта из `app/application/*/ports.py`); асерты на
   DTO/эффекты — образец `tests/application/test_quota_use_cases.py`.
2. API-тест (если новый роут): положите юзера фикстурой, `client` уже
   собирает контейнер на тестовой БД + FakeRedis; заголовок
   `{"Authorization": f"Bearer ..."}` или `internal_token` для
   `/api/admin/*`. Проверяйте и **отрицательные** ветки: гость → 403,
   чужой ресурс → 404, без токена → 401/403.
3. SQL-ветки — на реальной БД (фикстура `session`), N+1 —
   `assert_max_queries`.
4. Прогон: `uv run pytest tests/<dir> -q`, линт `uv run ruff check tests`.

### Новый экран/компонент (фронтенд)

1. Файл рядом с компонентом: `src/screens/Foo.test.tsx` (jsdom,
   `@testing-library/react` + `user-event`).
2. Сеть мокайте на уровне `fetch` (как `lib/marketPhotos.test.ts`) —
   проверяются и refresh-ретраи. Не мокайте внутренние модули без нужды.
3. Проверяйте UX-состояния: loading-скелетон, ошибку с «Повторить»,
   пустой список, гость → логин-промпт (образец `screens/Listing.test.tsx`).
4. Прогон с обязательным пулом:
   `npx vitest run src/screens/Foo.test.tsx --pool=threads --poolOptions.threads.minThreads=1 --poolOptions.threads.maxThreads=2`
   и `npm run build` (tsc).

## 7. CI (.github/workflows/ci.yml)

Два job'а на каждый push (main/master) и pull_request; артефактов нет
(зелёный/красный по exit code), кэш pip по setup-python.

| Job | Шаги (по порядку) | Детали |
|---|---|---|
| `tests` (ubuntu, Python 3.13) | checkout → setup-python (cache: pip) → `pip install -e . --group dev` + `ruff` → **ruff**: `ruff check app tests` → **pytest**: `pytest tests -q -p no:cacheprovider` (env `PYTHONIOENCODING=utf-8`) | Postgres/Redis в CI нет: тесты БД скипаются; полный прогон с БД — на среде разработчика |
| `security` (ubuntu, Python 3.13) | checkout → setup-python → `pip install -e .` + pip-audit + bandit → **pip-audit**: `pip-audit --skip-editable` (известные CVE в зависимостях) → **bandit**: `bandit -r app -ll --skip B101` (SAST, medium+, B101 — assert в app) | фронтенд (vitest/tsc) в CI сейчас не гоняется — прогонять локально командами из §1 |

Локальный аналог CI: `uv run ruff check app tests && uv run pytest -q -p
no:cacheprovider && pip install pip-audit bandit && pip-audit
--skip-editable && bandit -r app -ll --skip B101`.
