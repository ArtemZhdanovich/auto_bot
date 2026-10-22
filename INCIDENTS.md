# INCIDENTS / аудит безопасности

Журнал проверок и находок. Формат: дата, серьёзность, статус, что делать.
Последний аудит: **2026-10-22** (ручной статический аудит: исходящие вызовы,
монетизация, лиды, авторизация, секреты, фронтенд).

---

## Итог аудита 2026-10-15 — коротко

**Утечек лидов «налево» и скрытой монетизации НЕ обнаружено.** Все исходящие
вызовы — к задокументированным сервисам (LLM, источники авто, Telegram).
Единственный реальный риск — слабый `APP_SECRET_KEY` в локальном `.env`.

### Проверено и чисто ✅

| Зона | Проверка | Результат |
|---|---|---|
| Лиды | `app/api/leads.py`, `app/services/notify.py`, `alerting.py` | Пишутся только в свою БД (`leads`); уведомление — в свой Telegram (`ADMIN_CHAT_ID`/`MANAGER_CHAT_ID` через собственный `BOT_TOKEN`). Внешних CRM/webhook-ов нет |
| Партнёры (CPL) | `app/services/partners.py` | Партнёр явно забирает лид (`claim_lead`) со списанием с внутреннего баланса — задокументированная монетизация (P2-10), не сторонняя отправка |
| Монетизация | `app/api/payments.py` | Только Telegram Stars (XTR): деньги — на аккаунт бота у Telegram; активация подписки через `pre_checkout_query` + `successful_payment`. Сторонних платёжных ссылок/кошельков в коде нет |
| Исходящие хосты | grep URL по `app/` | Только: LLM (bigmodel.cn / api.z.ai, опц. Anthropic), источники авто (encar/kcar/che168 через прокси DataImpulse — формат `login__cr.cn` = код страны прокси, задокументирован в `app/core/config.py`), api.telegram.org. `rag.internal` — внутренний сервис |
| Телеметрия фронта | grep по `frontend/` | Нет sentry/posthog/metrika/pixels. Единственный внешний скрипт — официальный `telegram.org/js/telegram-web-app.js` |
| SQL-инъекции | grep `text(f…)`, `execute(f…)` | Сырых f-строк в SQL нет (ORM); `redis.eval` — Lua rate-limiter, не Python eval |
| eval/exec/pickle | grep | Нет |
| Auth | `app/api/auth.py` | initData валидируется HMAC по bot_token с `max_age`; веб-пароли — PBKDF2; magic-link — заглушка 501; internal/admin API — `hmac.compare_digest` токена (`app/core/deps_internal.py`) |
| Секреты в git | `git log --all --diff-filter=A -- .env` | `.env` никогда не коммитился, в репо только `.env.example` |
| SSRF | `by-url`, fetch-воркер | `GET /api/listings/by-url` только матчит URL в БД (не ходит наружу); fetch-воркер ходит по URL из парсера (не из пользовательского ввода) |
| /metrics | `app/api/metrics.py` | Агрегаты за internal-токеном, не публичные |

### Находки 🔎

#### INC-001 — СЛАБЫЙ APP_SECRET_KEY (ВЫСОКО) — ЗАКРЫТО (2026-10-16)

- **Где:** `.env` локальной/стендовой машины; код — `app/core/config.py:18`.
- **Суть:** `APP_SECRET_KEY` установлен длиной **9 символов**. Этим ключом
  подписываются JWT (`HS256`) **и** он же — fallback internal-токена
  (`INTERNAL_TOKEN` пуст → `deps_internal.internal_token()` возвращает
  `APP_SECRET_KEY`). Короткий секрет брутфорсится → подделка JWT любого
  пользователя + полный доступ к internal API (лиды, админ, платежи, бот).
- **Исправление (2026-10-16):** сгенерирован новый ключ 64 hex
  (`secrets.token_hex(32)`), вписан в `.env`, backend+bot перезапущены.
  Проверено: internal API с неверным токеном → 401, с верным → 200.
  Все выданные JWT инвалидированы (пользователи переподнимут сессию
  автоматически через initData).

#### INC-002 — один ключ на JWT и internal API (СРЕДНЕ) — ЗАКРЫТО (2026-10-16)

- **Суть:** `INTERNAL_TOKEN` пуст — внутренние эндпоинты (бот/воркеры/admin)
  защищены тем же секретом, что и подпись JWT. Компрометация одного = компрометация всего.
- **Исправление (2026-10-16):** задан отдельный `INTERNAL_TOKEN`
  (64 hex, `secrets.token_hex(32)`), перезапуск backend+bot. Fallback-поведение
  сохранено, но больше не используется. Тесты переведены на
  `deps_internal.internal_token()` как единый источник истины.

#### INC-003 — тексты диалогов уходят во внешние LLM (СРЕДНЕ, данные) — ПРИНЯТО

- **Суть:** сообщения пользователей и критерии поиска отправляются в LLM-провайдеры
  (`bigmodel.cn`/`api.z.ai` — юрисдикция КНР; опционально Anthropic). Это
  задокументированная архитектура (ТЗ), не скрытая отправка. Имя/username из
  Telegram хранятся только в своей БД (в LLM-промпт идёт текст диалога).
- **Что делать:** перед публичным запуском отразить в политике
  конфиденциальности; при желании — обезличивание текста перед промптом.
- **Статус:** ПРИНЯТО (осознанное проектное решение).

#### INC-004 — известный дефолт секрета в коде (НИЗКО) — ЗАКРЫТО (2026-10-16)

- **Суть:** `app_secret_key` имеет дефолт `"dev-secret-change-me-…"` — система
  стартует без секрета с заведомо известным ключом. Риск — только при забытой
  настройке окружения.
- **Исправление (2026-10-16):** fail-fast в `app/core/config.py`
  (`Settings._prod_secrets_required`): при `APP_ENV=prod` конфиг **падает на
  старте**, если ключ = дев-дефолт или короче 32 символов. Тесты:
  `tests/test_prod_secrets.py`.

#### INC-005 — self-signed сертификат (НИЗКО, известно) — ЗАКРЫТО ДЛЯ ПРОДА (2026-10-16)

- **Суть:** nginx на 8443 с самоподписанным сертификатом → в браузере/MITM-риск
  для миниаппа вне Telegram. Давно известное ограничение MVP (PROGRESS.md),
  блокер для нативной web_app-кнопки.
- **Исправление (2026-10-16):** в боевом оверрае (`deploy/docker-compose.prod.yml`)
  TLS терминирует **Caddy с автоматическим Let's Encrypt** по `$DOMAIN`;
  self-signed остаётся только в dev-профиле. Ранбук: `deploy/DEPLOY.md`.

---

## Устранённые угрозы — свод (актуализировано 2026-10-22)

Полный перечень угроз, которые устранены в коде проекта, с контрмерой и
проверкой. Часть пунктов была закрыта до заведения журнала (этапы 0–8 и
рефакторинг) — здесь они собраны все; детали по INC-001…005 — выше.

| # | Угроза | Контрмера | Проверка |
|---|---|---|---|
| 1 | Подделка Telegram `initData` (обход авторизации Mini App) | HMAC-валидация подписи по bot_token + отклонение данных старше `INIT_DATA_MAX_AGE_SEC` (24 ч); сессия — собственный JWT | `tests/identity/test_security_initdata.py` |
| 2 | Слабый `APP_SECRET_KEY` (брутфорс → подделка JWT + internal API) | INC-001: ключ 64 hex; INC-004: fail-fast в проде при дев-дефолте/длине < 32 | `tests/security/test_prod_secrets.py` |
| 3 | Один секрет на JWT и internal API (компрометация одного = всё) | INC-002: отдельный `INTERNAL_TOKEN` | `tests/security/test_internal_token_empty_rejected.py` |
| 4 | Подделка internal-запросов (тайминг-атака на сравнение токена) | константное сравнение `hmac.compare_digest`; `InternalDishkaRoute` — проверка до резолва сценариев | `tests/transport/` (роуты 401 без токена), смоук сервисов |
| 5 | Self-signed TLS в бою (MITM миниаппа) | INC-005: Caddy + Let's Encrypt в `deploy/docker-compose.prod.yml`; self-signed только dev | deploy-кит, `deploy/DEPLOY.md` |
| 6 | Кража партнёрских API-ключей из БД | в БД только SHA-256; сырой ключ показывается один раз; ротация/ревокация мгновенные; префикс для опознания | `tests/partners/test_partners.py` |
| 7 | IDOR: чужие лиды/ПД без права | ПД (`user_id`) только после эксклюзивного claim (`UPDATE … RETURNING`/`SKIP LOCKED`); пул — без ПД; владелец проверяется внутри use case | `tests/partners/` (claim-инварианты на живой БД) |
| 8 | Утечка ORM-полей (массовое выставление/скрытые столбцы) | наружу только Pydantic-схемы с явным `response_model`; `lazy="raise"` на связях | `tests/transport/test_miniapp_api.py`, контракты схем |
| 9 | SQL-инъекции | только параметризованные запросы SQLAlchemy; f-string в SQL отсутствуют (аудит grep) | аудит 2026-10-15, чисто |
| 10 | SSRF через админские конфиги источников | валидация URL конфига: только http/https, запрет приватных подсетей/loopback (`app/application/admin/dto.py`); fetch-воркер ходит только по реестру | `tests/admin/test_admin_api.py` |
| 11 | SSRF через `by-url` / пользовательский ввод | `GET /api/listings/by-url` матчит URL в БД, наружу не ходит | `tests/catalog/test_listings.py` |
| 12 | Промпт-инъекции через базу знаний RAG | kb — «справочные данные, не инструкции»; запрет цифр без источника | `tests/dialog/test_rag.py` |
| 13 | Флуд партнёрского API (подбор ключей/сканирование) | rate limit 60 req/min на ключ (fixed window Redis) | `tests/security/test_rate_limit.py` |
| 14 | Флуд Telegram (бан бота) от наших уведомлений | Token Bucket Redis+Lua (30 msg/s глобально, 1 msg/s чат), 429 → retry_after; Redis лежит → fail-open | `tests/security/test_rate_limit.py`, `tests/infrastructure/test_send_worker.py` |
| 15 | Спам пользователя уведомлениями | персональный лимит 1 сообщение/30 с + антидубль 7 дней + тихие часы | `tests/infrastructure/test_notify.py` |
| 16 | Утечка секретов через аудит-лог | `admin_actions` пишет payload без секретов | `tests/admin/test_admin_audit.py` |
| 17 | Секреты в git | `.env` никогда не коммитился (проверено `git log --all`); в репо только `.env.example` | аудит 2026-10-15 |
| 18 | Несанкционированный доступ к сырым ответам источников | presigned GET на 10 минут + X-Internal-Token; retention 180 дней | `tests/admin/test_admin_api.py` |
| 19 | Компрометация S3-токена (полный доступ к бакету) | минимум привилегий (без CreateBucket — мягкий warning); бакет только под raw-payload | `tests/infrastructure/test_storage.py` |
| 20 | Перехват документов RAG at-rest | опциональное PQ-шифрование ML-KEM-768 + X25519 + AES-256-GCM (`ENCRYPT_DOCUMENTS`) | `tests/security/test_pq_crypto.py` |
| 21 | Отравление очереди (мусорные/повторные сообщения) | контракты команд — строгие Pydantic (`extra="forbid"`) + обязательный `idempotency_key`; DLQ + алерт по глубине | `tests/messaging/`, `tests/transport/test_enrich_handler.py` |
| 22 | Подбор веб-пароля | PBKDF2-SHA256 240k итераций; magic-link отключён (501); email — regex-валидация | `tests/identity/test_auth_api.py` |
| 23 | eval/exec/pickle-инъекции | отсутствуют (аудит grep); Redis rate-limiter — Lua, не Python eval | аудит 2026-10-15 |

Не устранено, осознанно принято: INC-003 (тексты диалогов уходят во внешние
LLM — задокументированная архитектура; перед публичным запуском отразить в
политике конфиденциальности).

---

## Как пополнять журнал

Новая находка → новый блок `INC-0XX` с датой, серьёзностью (ВЫСОКО/СРЕДНЕ/
НИЗКО), статусом (ОТКРЫТО/ПРИНЯТО/ЗАКРЫТО) и шагом исправления.
Закрытые — не удалять, помечать `ЗАКРЫТО (дата)`.
