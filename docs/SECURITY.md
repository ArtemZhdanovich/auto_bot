# Безопасность

Модель безопасности проекта; журнал аудитов и инцидентов —
[INCIDENTS.md](../INCIDENTS.md) (INC-001…005 + свод устранённых угроз).
Эксплуатация секретов/деплоя — [OPERATIONS.md](OPERATIONS.md),
инцидент-рецепты — [RUNBOOK.md](RUNBOOK.md).

Карта кода: токены/инициализация — [app/infrastructure/security/tokens.py](../app/infrastructure/security/tokens.py),
internal-API — [app/transport/http/deps/internal.py](../app/transport/http/deps/internal.py),
партнёрские ключи — [app/infrastructure/security/partner_keys.py](../app/infrastructure/security/partner_keys.py),
IP-лимиты — [app/infrastructure/security/ip_limit.py](../app/infrastructure/security/ip_limit.py),
SSRF link-preview — [app/infrastructure/services/link_preview.py](../app/infrastructure/services/link_preview.py),
маскировка — [app/application/chat/privacy.py](../app/application/chat/privacy.py) +
[app/domain/shared/contacts.py](../app/domain/shared/contacts.py),
платежи — [app/application/payments/use_cases.py](../app/application/payments/use_cases.py).

## 1. Модель угроз (кратко)

Атакующийsurface: анонимный веб-гость, владелец легитимного аккаунта
(пытается читать чужое / обходить тарифы), партнёр с API-ключом,
компрометация БД/секретов, вредоносный контент (ссылки в чатах, описания
лотов), replay-платежи. Ключевые контрмеры и их проверки — сводная таблица
в [INCIDENTS.md](../INCIDENTS.md) (23 позиции: initData-подделка, слабые
секреты, IDOR, SQL-инъекции, SSRF, промпт-инъекции RAG, флуд, утечка
ключей, PQ-шифрование и др.). Осознанно принято: INC-003 — тексты диалогов
уходят во внешний LLM (api.z.ai/bigmodel.cn; отразить в политике
конфиденциальности до публичного запуска).

## 2. Аутентификация (все флоу)

### 2.1 Mini App: initData → JWT

`POST /api/auth/telegram` ([app/infrastructure/security/tokens.py](../app/infrastructure/security/tokens.py),
`validate_init_data`):

- подпись: HMAC-SHA256 c ключом `HMAC(b"WebAppData", BOT_TOKEN)`;
  `data_check_string` собирается из **сырых** URL-encoded значений;
  секрет-ключ бота не передаётся клиенту никогда;
- возраст `auth_date` ≤ `INIT_DATA_MAX_AGE_SEC` (24 ч), будущее ≥ 60 с —
  отказ; длина initData ≤ 16 KB (анти-DoS на парсинг);
- **дубликаты ключей запрещены** (парсеры выбрали бы разные значения);
  hash обязан быть hex; сравнение — `hmac.compare_digest`;
- WORKAROUND: часть клиентов (Telegram Desktop) подписывает decoded-значения —
  принимается как fallback, но данные читаются из того варианта, чья
  подпись совпала (log `initdata_decoded_variant_used`);
- успех → JWT-пара (`sub=telegram_id`, `type=access|refresh`, HS256,
  leeway 10 с; алгоритм `none` невозможен — Literal в Settings).

Тесты: `tests/identity/test_security_initdata.py`.

### 2.2 Веб: email+пароль и «Войти через Telegram»

- `POST /api/auth/register` / `POST /api/auth/login`: PBKDF2-SHA256;
  тайминг выровнен — PBKDF2 выполняется всегда (dummy-хеш для
  несуществующего email), «email нет» неотличим от «пароль неверен» (401);
  деактивированный — 403; email — regex-валидация, пароль 8–128.
- `POST /api/auth/magic-link` — заглушка **501** (не включали осознанно).
- Вход через бота (`/api/auth/telegram/link*`): одноразовый код + HttpOnly
  **state-cookie** (`tglogin_state`, path-ограниченная, `SameSite=lax`,
  в prod `Secure`; `none` только для cross-site-доменов) — код бесполезен
  чужому браузеру. Session-fixation-фикс: internal-эндпоинты
  `/{code}/context|confirm|DELETE /{code}` (X-Internal-Token) отдают боту
  IP/UA/время инициатора — бот показывает их юзеру и зовёт `confirm`
  только после явного «✅ Это я»; жертва может отменить код. `state_hash`
  проверяется при выдаче JWT.

### 2.3 Гостевые сессии

`POST /api/auth/guest`: юзер «Гость» с синтетическим `telegram_id < 0`,
без email/пароля; сразу JWT. TTL гостя = TTL токенов (access 1 ч /
refresh 30 д), отдельного guest-TTL нет — гостевость определяется
отрицательным id на каждом защищённом эндпоинте (`_reject_guest` → 403:
чаты, ws-токен, размещения, анализ, платежи). Анти-флуд: фиксированное окно
Redis — ≤ `GUEST_IP_RATE_LIMIT` (5) созданий/мин с IP (`X-Forwarded-For`
первый, fallback `request.client.host`), ключ `rl:guest:{ip}:{bucket}`,
сверх — 429; Redis лежит → fail-open (гостей не блокируем).

### 2.4 Refresh

`POST /api/auth/refresh`: проверка подписи/типа/`sub`, пользователь
обязан существовать и быть активным. **Ротации refresh нет** (осознанный
TECHDEBT: нужен `users.refresh_token_version`); украденный refresh живёт
до 30 дней — mitigate коротким `ACCESS_TOKEN_TTL_SEC` и ротацией
`APP_SECRET_KEY` (инвалидирует все сессии; юзеры переподнимают initData
автоматически).

## 3. Авторизация: роли, владение, IDOR

- **Роли** `users.role ∈ {buyer, dealer, salon, admin}`; тарифные гейты —
  по `subscription_tier` (free/pro/business) + `subscription_until`
  (доменные политики `app/domain/subscription/policies.py`). Права
  проверяются **в сценариях**, не в транспорте: гость → 403 (чаты/анализы/
  размещения/ws), тарифные чеки (Pro+ AI, агент/чеки — Business),
  деактивированный (`is_active=false`) → 403 на логине и 401 на refresh.
- **Владение (owner-чеки)**: чужие лоты/треды/платежи → **404 без
  раскрытия факта существования**. Чаты: участник видит только свои треды —
  и в REST (`ListMessages`/`SendMessage` проверяют участие,
  [app/application/chat/use_cases.py](../app/application/chat/use_cases.py)),
  и в websocket: каждая подписка на канал `user:`/`partner:`/`thread:`
  проходит **subscribe-proxy** centrifugo → `POST /api/chats/subscribe-proxy`
  (тело v6 `{client, channel, user}`, user = sub connection-JWT; разрешено
  `{}`, запрещено `{"error":{"code":403}}`). Governance переговорных:
  инвайты/кик — только owner.
- **Партнёры**: `X-API-Key`; правило «ключ active И аккаунт active» — в
  `SqlAlchemyPartnerRepository`. Лиды: ПД (`user_id`) отдаются только после
  эксклюзивного claim (`UPDATE … RETURNING`/`SKIP LOCKED`) — один лид
  нельзя забрать дважды; пул без ПД. IDOR-тесты: `tests/partners/`
  (claim-инварианты на живой БД), `tests/catalog/test_chats*.py`.
- **Internal/admin**: все `/api/admin/*`, `/metrics`,
  `/api/auth/telegram/link/{code}/*` — route-класс `InternalDishkaRoute`
  (проверка **до** резолва сценариев; эндпоинт не может «забыть» токен).
  Сравнение `hmac.compare_digest` по байтам; оба токена пусты → **503
  fail-closed**, а не открытый доступ.
- **ORM-утечки**: наружу только Pydantic-схемы с явным `response_model`
  (хеши паролей/ключи не сериализуются); связи `lazy="raise"`.

## 4. Rate-limits (что и где)

| Лимит | Где | Механика | Поведение при исчерпании |
|---|---|---|---|
| Guest-сессий с IP | `POST /api/auth/guest` | fixed window Redis `rl:guest:{ip}:{min}`, `GUEST_IP_RATE_LIMIT=5`/мин | 429 |
| Партнёрский API | все `X-API-Key`-роуты | fixed window per partner_id, `PARTNER_RATE_LIMIT_PER_MIN=60` | 429 |
| Telegram Bot API | send-воркер | Token Bucket Redis+Lua: `TELEGRAM_RATE_GLOBAL_PER_SEC=30` глобально, `=1`/чат | сглаживание (wait), 429 → retry_after; Redis лежит → fail-open (страховка — 429-обработка) |
| Уведомления юзеру | send-воркер | ≥1 msg/`NOTIFY_RATE_LIMIT_SEC=30` c, антидубль `notified:{uid}:{lid}` 7 д, тихие часы | пропуск (не ошибка) |
| AI-квоты | dialog/analysis | `ai_daily_limit` по тарифу + Redis-счётчики; лок хода `dlg:turn-lock:{uid}` (анти double-tap) | 429/403 по тарифу |
| Admin/internal | — | без лимита (закрыты токеном; сеть/ingress ограничивают) | — |

## 5. SSRF (link-preview и конфиги источников)

OG-превью ссылок в чатах ([app/infrastructure/services/link_preview.py](../app/infrastructure/services/link_preview.py)):

- только `http`/`https`; хост резолвится `getaddrinfo` и **все** A/AAAA
  обязаны быть публичными: private/loopback/link-local/reserved/multicast/
  unspecified → `LinkBlockedError` → 400 (DNS-rebinding с одним публичным
  и одним приватным A не проходит — проверяется каждая запись);
- ≤ 1 редирект, каждый хоп перепроверяется тем же фильтром
  (`follow_redirects=False`, Location вручную); тело ≤ 512 KB (OG-теги),
  таймаут 3 c; кэш 24 ч; фиксированный UA `auto-bot-preview/1.0`.
- Админские конфиги источников (`PUT /api/admin/sources/{name}`): URL
  валидируется `is_safe_external_url` (http/https, не private/loopback/
  link-local); `GET /api/listings/by-url` только матчит URL в БД — наружу
  не ходит. Fetch-воркер ходит по реестру адаптеров, не по пользовательскому
  вводу.

Тесты: `tests/infrastructure/test_delivery.py` (link-preview),
`tests/admin/test_admin_api.py`, `tests/catalog/test_listings.py`.

## 6. Маскировка контактов (privacy)

[app/application/chat/privacy.py](../app/application/chat/privacy.py) →
[app/domain/shared/contacts.py](../app/domain/shared/contacts.py):
телефоны (`PHONE_RE`) и ссылки внешних мессенджеров (`TME_RE`,
`WHATSAPP_RE`) заменяются плейсхолдерами (`PHONE_MASK`/`MESSENGER_MASK`)
**до** валидации длины сообщения. Применение — `SendMessage` по типу треда:
seller/deal/group-чаты маскируются (обмен контактами только через сделку),
**support-треды не маскируются** (оператор = площадка). Наш бот и
share-ссылки не трогаются. Те же паттерны переиспользует авто-модерация
лотов рынка (`contacts_in_text`). Тесты: `tests/application/test_chat_privacy.py`.

Приватность чатов: участники видят только свои треды (§3); операторы —
support; retention — скрытие сообщений через 90 дней после закрытия треда.

## 7. Платежи (Telegram Stars, XTR)

- **Сумма считается один раз на сервере** (`TierConfig.price` в
  `CreatePendingPayment`); клиенту — только отображение. Провайдер —
  Telegram Stars (`provider_token=""`, currency XTR): сторонних платёжных
  ссылок/кошельков нет.
- **pre_checkout whitelist**
  ([app/transport/bot/handlers.py](../app/transport/bot/handlers.py),
  `@router.pre_checkout_query()`): payload обязан быть `sub:<payment_id>`
  (подписка) или `plc:<payment_id>` (продление размещения), где id — цифры;
  иначе `answer(ok=False)` — списания нет. Инвойс-кнопка троттлится 10 c
  на юзера.
- **Идемпотентность `successful_payment`**
  ([app/application/payments/use_cases.py](../app/application/payments/use_cases.py)):
  платёж и эффект меняются в одной транзакции; `mark_paid` — **условный
  UPDATE** (`… WHERE status <> 'paid'`): параллельный/повторный вебхук
  получает `False` → rollback без второго продления; ранее обработанный
  (`status == 'paid'`) — ранний возврат. Ретраи бэкоффом 2/4 с ×3;
  исчерпание → алерт админу (ручная активация), юзеру — менеджер.
  `provider_payment_id` (telegram_payment_charge_id) сохраняется.
- **Replay**: чужой payment_id отвергается (`record.user_id != user_id` →
  404); уникальные ключи платежей/лидов (миграция 0025 leads idempotency).
  Тесты: `tests/billing/test_payments_api.py`, `tests/bot/test_bot_payments.py`.

## 8. Секреты: где живут и ротация

- **Где**: `.env` на сервере (в git не попадал — проверено
  `git log --all`, в репо только `.env.example`); в образ/контейнеры —
  `env_file`; Centrifugo получает их sed-подстановкой в конфиг. Все сек
 реты в коде — `SecretStr` (не утекают в repr/логи). Compose публикует
  прод-секреты через `<placeholder>`: `DB_PASSWORD`, `INTERNAL_TOKEN`,
  `CENTRIFUGO_SECRET`, `CENTRIFUGO_HTTP_API_KEY`, `RAG_JWT_SECRET`,
  `TRANSLATE_API_TOKEN`, `GLM_API_KEY`, `PQ_*`.
- **Fail-fast в prod** (`APP_ENV=prod`): дев-дефолт/короткий
  `APP_SECRET_KEY`, пустой `INTERNAL_TOKEN`, `change-me` у Centrifugo,
  дефолтные/`guest:guest` DB/Rabbit URL — старт падает
  (`Settings._prod_secrets_required`, INC-001/002/004).
- **Ротация**:
  - `APP_SECRET_KEY` — инвалидирует все JWT (юзеры переподнимут сессию
    через initData); перезапуск backend+bot+воркеров;
  - `INTERNAL_TOKEN` — отдельно от JWT-секрета; синхронно в `.env`
    backend-процессов **и** в конфиге centrifugo (`__INTERNAL__`),
    иначе subscribe-proxy начнёт отдавать 503;
  - `CENTRIFUGO_SECRET`/`CENTRIFUGO_HTTP_API_KEY` — рестарт centrifugo и
    backend/chat (ws-токены живут ≤12 ч);
  - партнёрские ключи — `POST /api/admin/partners/{id}/rotate|revoke`
    (см. [RUNBOOK.md](RUNBOOK.md) Р-1);
  - `BOT_TOKEN` — через @BotFather (revoke old), рестарт бота;
  - LLM-ключи — панель провайдера.

## 9. Аудит-логи

- `admin_actions` — действия операторов (создание/правка источников,
  ручной fetch, модерация, rotate/revoke ключей): payload пишется **без
  секретов**; `tests/admin/test_admin_audit.py`.
- Ключевые события в логах: `subscription_activated`, `placement_extended`,
  `pre_checkout_bad_payload`, `subscribe_denied` (channel/sub),
  `initdata_decoded_variant_used`, `jwt_decode_failed` (без деталей
  наружу), `payment_activation_failed_after_retries`. Каждый HTTP-запрос
  несёт trace_id (TraceIdMiddleware).
- Идемпотентность сообщений очереди: контракты — строгие Pydantic
  (`extra="forbid"`) + `idempotency_key` (заголовок у POST сообщений
  чатов); DLQ + алерт по глубине (§ очередь в OPERATIONS.md).

## 10. Заголовки / CSP / CORS

- **Centrifugo**: `client.allowed_origins: ["*"]` в
  [configs/centrifugo.json.tmpl](../configs/centrifugo.json.tmpl) —
  dev-значение; в проде сузить до домена миниаппа (prod-чеклист).
- **nginx миниаппа** ([frontend/nginx.conf](../frontend/nginx.conf)):
  `index.html` — `Cache-Control: no-cache` (анти-поломки вебвью после
  деплоя), ассеты — immutable; CSP-заголовки не выставляются (SPA без
  inline-скриптов; единственный внешний скрипт — официальный
  `telegram.org/js/telegram-web-app.js`; телеметрии во фронте нет).
  Прод-TLS терминирует Caddy (Let's Encrypt) — INC-005.
- **Бэкенд**: CORS-middleware не сконфигурирован (API зовётся с того же
  origin через nginx-прокси `/api`); секретные эндпоинты закрыты
  токенами, а не origin-политиками.
- Самоподписанный серт miniapp :8443 — только dev; Telegram его не
  принимает (см. OPERATIONS.md §6).

## 11. Чек-лист при деплое

1. `.env`: все `<secret>` заполнены; `APP_ENV=prod` (fail-fast проверит
   секреты/URL'ы сам — убедиться, что сервис стартует, а не падает).
2. `APP_SECRET_KEY` ≥ 32 симв. (64 hex), `INTERNAL_TOKEN` ≠ APP_SECRET_KEY,
   `CENTRIFUGO_*` ≠ change-me, `DB_PASSWORD`/`RABBITMQ_URL` без дефолтов.
3. Миграции проверены на копии: `upgrade head` → `downgrade -1` → `upgrade head`.
4. TLS: Caddy + Let's Encrypt (`deploy/docker-compose.prod.yml`); UFW:
   наружу только 22/80/443; 5432/6379/5672/9000/15672 закрыты.
5. `client.allowed_origins` centrifugo сужен; self-signed :8443 не публичен.
6. `ALERTS_ENABLED=true`, `ADMIN_CHAT_ID`/`MANAGER_CHAT_ID` живые;
   cron бэкапа 03:00 + тест восстановления.
7. Смоук безопасности после подъёма:
   ```bash
   curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000/metrics            # 403 без токена
   curl -s -o /dev/null -w "%{http_code}\n" -H "X-Internal-Token: <secret>" http://localhost:8000/metrics  # 200
   curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000/api/admin/sources   # 403 без токена
   curl -s -o /dev/null -w "%{http_code}\n" -X POST http://localhost:8000/api/auth/guest  # 201; 6-й раз с IP — 429
   ```
8. Проверить прогон `tests/security` на стенде: `uv run pytest tests/security -q`.
9. Политика конфиденциальности опубликована (INC-003: тексты диалогов
   уходят во внешний LLM).
