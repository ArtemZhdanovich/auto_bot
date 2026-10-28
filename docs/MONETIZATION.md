# Монетизация: тарифы, Telegram Stars, рынок объявлений, партнёрская экономика

> Источник правды — код. Ключевые файлы:
> `app/domain/subscription/policies.py`, `app/application/payments/use_cases.py`,
> `app/application/billing/dto.py`, `app/infrastructure/db/repositories/payments.py`,
> `app/application/market/` (+ `moderation.py`, `dto.py`, `quality.py`),
> `app/application/partners/`, `app/domain/partners/pricing.py`,
> `app/infrastructure/services/partners/repository.py`,
> `app/infrastructure/db/repositories/market_lots.py`,
> `app/infrastructure/workers/send.py`, миграции
> `app/infrastructure/db/migrations/versions/` (0006, 0014, 0015, 0024, 0027, 0030),
> фронт — `frontend/src/screens/Profile.tsx`.

Документ описывает **четыре денежных контура** проекта:

1. **Подписки** Free / Pro / Business — оплата Telegram Stars (`kind='subscription'`).
2. **Внутренний рынок объявлений** — бесплатный период, включённые лоты платных
   тарифов, продление размещений за Stars (`kind='placement'`), партнёрские
   пакеты слотов за баланс.
3. **Партнёрская экономика** — CPL-лиды (claim/batch), баланс/транзакции,
   авто-Business владельцу счёта.
4. **Каденсы и экспирация** — дайджесты по тарифу, напоминания/даунгрейд
   подписки, экспиратор размещений.

---

## 1. Тарифы: правила, лимиты, иерархия

Реализация: `app/domain/subscription/policies.py` (константы `TIERS`,
`ANNUAL_DISCOUNT = 0.20`, функции `tier_rank`, `can_upgrade`, `effective_tier`,
`activate_subscription`, `grant_partner_business`).

### 1.1 Таблица «тариф → фичи → лимиты»

| Параметр | Free | Pro | Business |
|---|---|---|---|
| Цена, Stars/мес (`price_stars_month`) | 0 | **500** | **2500** |
| Цена, Stars/год (`price_stars_year`) | 0 | 4800 (−20%) | 24000 (−20%) |
| Фильтры-уведомлений (`max_filters`) | 3 | 20 | без лимита (`None`) |
| AI-поиск, сообщений-выдач/день (`ai_daily_limit`) | 3 | 200 | без лимита |
| AI-разбор цены (`analyses_limit`) | **1 в неделю** | **30 в день** | без лимита |
| Окно разборов (`analyses_window_days`) | 7 | 1 | — |
| Каденс дайджеста (`digest`) | `weekly` (раз в неделю, утро пн) | `daily` (утро+вечер) | `instant` (при публикации; буфер досылается утром/вечером) |
| Чеки осмотра (`inspection`) | нет (403) | да | да |
| Instant-уведомление при публикации лота (`instant`) | нет | нет | да |
| Агент на сделку (`agent`) | нет | нет | да |

Пояснения по коду:

- `TierConfig.price(period_days)`: `period_days >= 365` → годовая цена
  (`int(месяц * 12 * 0.8)`), иначе месячная. Сумма считается **только** в
  сценарии (`CreatePendingPayment`), транспорт её лишь показывает.
- `TIER_ORDER = ("free", "pro", "business")`, `tier_rank()` — позиция в
  иерархии. Неизвестный тариф → ранг 0 (безопасный дефолт).
- `can_upgrade(current, offered)` — источник правды апселла в Mini App и боте:
  предлагаем только тарифы **строго выше** текущего (business не видит кнопку
  Pro — раньше это был баг).
- `effective_tier(user)`: платный тариф с истёкшим `subscription_until` = `free`;
  `business` c `subscription_until IS NULL` — **бессрочный** партнёрский тариф;
  «pro без даты» считается просроченным артефактом → `free`.
- `activate_subscription(user, tier, period_days)` — повторная оплата
  **суммируется** с текущим сроком: `base = max(now, действующий until)`,
  `new_until = base + period_days`.
- `grant_partner_business(user)` — выдаёт `subscription_tier='business'` +
  `subscription_until=None` (вечный Business клиенту/владельцу партнёра).

### 1.2 Где лимиты реально применяются

| Лимит | Потребитель |
|---|---|
| `analyses_limit` / `analyses_window_days` | `app/application/analysis/use_cases.py` (`RunAnalysis._quota_left`) + `app/domain/pricing/rules.py` (`ANALYSES_LIMITS`, `ANALYSES_WINDOW_DAYS`, `ensure_quota`) |
| `ai_daily_limit` | `app/application/dialog/use_cases.py::ai_daily_limit` → агент `run_agent` (списание за выдачу результата, не за сообщение) |
| `max_filters` | сценарии фильтров/уведомлений |
| `digest` | `app/infrastructure/workers/send.py::_digest_due` (free — только утренний слот понедельника; evening — pro/business) |
| `inspection` | `RunAnalysis._inspection_for_deep_analysis` — чек подмешивается в контекст LLM только тарифам с доступом |
| `instant` / `agent` | сортировка фильтров в режимы instant/digest (`notify_new_listings`), `/api/deals/{id}/agent` — только Business |

### 1.3 Витрина тарифов в Mini App (Profile)

`frontend/src/screens/Profile.tsx`:

- `GET /api/subscription` → карточка текущего тарифа: `tier`, `subscription_until`
  (у business без даты — «бессрочная (партнёрская)»), строка лимитов
  (`analyses_limit`, `digest`, `inspection`, `instant`, `agent`, `max_filters`).
- Цены кнопок — из `prices` ответа (`pro_month`, `pro_year`, `business_month`)
  с фолбэками 500/4800/2500.
- Тариф-грид `TIER_FEATURES` — сравнение колонок free/pro/business; кнопки
  покупки рендерятся по `TIER_RANK` (иерархия из `policies.py`).
- Покупка: `POST /api/payments/stars {tier, period}` → `invoice_url` →
  `openInvoice` (Telegram WebApp); статусы `paid/failed/cancelled/pending`
  показываются сообщением в карточке.

---

## 2. Оплата Telegram Stars: подписки и размещения

Единый платёжный контур: таблица `payments`, провайдер Stars
(`STARS_CURRENCY = "XTR"`, `STARS_PROVIDER = "stars"` —
`app/application/billing/dto.py`).

### 2.1 Схема БД `payments` (миграции 0001, 0027)

`app/infrastructure/db/models.py::Payment`:

| Колонка | Тип | Назначение |
|---|---|---|
| `id` | BigInteger PK | номер pending-платежа, едет в payload `sub:<id>` / `plc:<id>` |
| `user_id` | FK users.telegram_id | плательщик |
| `amount` | Numeric(12,2) | сумма в Stars |
| `currency` | String(8) | `XTR` |
| `provider` | String(16) | `stars` (CHECK допускает `yookassa`, `crypto`) |
| `provider_payment_id` | String(128) | `telegram_payment_charge_id` |
| `status` | String(16) | `pending` → `paid` |
| `tier` | String(16) | тариф подписки (`NULL` для placement) |
| `period_days` | Integer | 30/365 для подписки; `7*weeks` для размещения |
| `kind` | String(16) | **CHECK `kind IN ('subscription','placement')`**, default `'subscription'` (миграция 0027) |
| `listing_id` | BigInteger | предмет `kind='placement'` |
| `created_at` / `paid_at` | timestamptz | |

Идемпотентность вебхуков: `UniqueConstraint (provider, provider_payment_id)`
+ условный `mark_paid` (см. §2.4). Индекс `idx_payments_user_created`.

Поля подписки пользователя (`users`, миграция 0001):
`subscription_tier` CHECK `IN ('free','pro','business')` + `subscription_until`
(timestamptz, `NULL` — free или бессрочный партнёрский Business).

### 2.2 Флоу оплаты подписки (Mini App)

```
 Mini App (Profile.tsx)          Backend (HTTP)              Telegram                Bot
 ───────────────────────         ──────────────              ─────────               ────
 1. Кнопка «Pro 500★/мес»
    │
    ├─POST /api/payments/stars──▶ 2. CreatePendingPayment
    │   {tier:"pro",             │   TIERS[tier].price(30)
    │    period:"month"}         │   INSERT payments(status='pending',
    │                            │          kind='subscription')  [commit]
    │                            │   3. POST /bot<token>/createInvoiceLink
    │                            │      payload="sub:<payment_id>",
    │                            │      currency=XTR, prices=[{amount}]
    │◀──{payment_id, invoice_url, amount}──┘
    │
 4. Telegram.WebApp.openInvoice(url)
    │                            │
    ├────────── пользователь подтверждает списание Stars ──────────▶
    │                            │
    │                            │            5. PreCheckoutQuery ─▶ bot: handlers.py
    │                            │               payload="sub:<id>"   pre_checkout_query()
    │                            │               валиден? → answer(ok) (whitelist sub:|plc:,
    │                            │               иначе ok=False «счёт устарел»)
    │                            │
    │                            │            6. Message(successful_payment) ─▶ bot:
    │                            │               charge_id=telegram_payment_charge_id
    │                            │
    │                            │◀──POST /api/payments/success (internal, X-Internal-Token)
    │                            │   {payment_id, user_id, provider_payment_id}
    │                            │   7. ConfirmPayment (см. §2.4)
    │                            │   user.subscription_tier=tier
    │                            │   user.subscription_until=max(now,until)+period
    │                            │   payments.status='paid'  [одна транзакция, commit]
    │                            │──▶{activated, tier, subscription_until,
    │                            │      kind:"subscription"}
    │                            │            8. Бот: «Тариф … активирован до …»
 9. callback openInvoice('paid') → Profile перечитывает /api/subscription
```

Шаги 5–8 выполняет Telegram-бот (`app/transport/bot/handlers.py`):
`pre_checkout_query` (строка ~849), `on_successful_payment` (~870).
Ретраи активации: до 3 попыток с бэкоффом 2/4 сек; при полном отказе — алерт
админу (`payment_activation_failed_after_retries`) и сообщение пользователю
«напишите менеджеру».

### 2.3 Флоу оплаты продления размещения (kind='placement')

```
 Mini App (Market/MyLots)        Backend                                Telegram
 ───────────────────────         ──────                                 ────────
 1. «Продлить на 1/4 нед»
    ├─POST /api/market/lots/{id}/extend {weeks:1|4}─▶ 2. ExtendLot (use_cases.py)
    │   только status accepted|expired, иначе 409     │   weeks∈(1,4) иначе 400
    │                                                │   цена: 1 нед = market_week_stars (20★)
    │                                                │         4 нед = market_week_stars*3 (60★)
    │                                                │   INSERT payments(status='pending',
    │                                                │     kind='placement', listing_id,
    │                                                │     period_days=7*weeks)  [commit]
    │                                                │   3. createInvoiceLink
    │                                                │      payload="plc:<payment_id>"
    │◀──{payment_id, invoice_url, amount, weeks}──────┘
 4. openInvoice → PreCheckout (plc:<id>) → успешная оплата
    │                                                │   5. successful_payment → бот →
    │                                                │      POST /api/payments/success
    │◀─ ConfirmPayment._confirm_placement:
    │     base = max(now, placement_free_until, placement_paid_until)
    │     placement_paid_until = base + 7*weeks дней
    │     is_active=True, status='accepted'   ← оживляет expired/needs_payment
    │     payments.status='paid'              [одна транзакция]
    │  Бот: «✅ Оплата принята (N Stars). Объявление продлено до YYYY-MM-DD»
```

Ключевые файлы: `ExtendLot` и `_confirm_placement` в
`app/application/payments/use_cases.py`, `create_pending_placement` /
`save_placement_paid_until` в `app/infrastructure/db/repositories/payments.py`.

### 2.4 Идемпотентность и гонки в платежах

| Гонка | Защита |
|---|---|
| Двойная доставка `successful_payment` (ретрай Telegram) | `ConfirmPayment`: если `record.status == 'paid'` — вернуть текущее состояние без повторного эффекта |
| Два параллельных вебхука одного платежа | `mark_paid` — условный `UPDATE payments SET status='paid' … WHERE id=:id AND status != 'paid'`; `rowcount == 0` → эффект уже применён другой транзакцией, сценарий делает rollback и возвращает актуальные дедлайны |
| «Полуоплаченная» подписка (paid без подписки) | платёж + подписка меняются в одной транзакции, `commit` один (порт `PaymentRepository.commit`) |
| Подделанный/чужой payload в инвойсе | бот-хендлер `pre_checkout_query`: whitelist `payload.startswith(("sub:", "plc:"))`, ровно 2 части, вторая — цифры; иначе `answer(ok=False)` и списание не происходит |
| Чужой `listing_id` в placement-платеже | `save_placement_paid_until` обновляет только `WHERE owner_type='user' AND owner_user_id=:user_id` |
| Двойной клик по кнопке тарифа в боте | `_SUB_INVOICE_COOLDOWN_SEC = 10` (in-memory кулдаун на `user_id`, handlers.py, TECHDEBT 2.10) |
| Backend лежал в момент оплаты | 3 ретрая с бэкоффом, затем алерт админу для ручной активации (TECHDEBT 2.9) |
| Один `provider_payment_id` дважды | `UNIQUE (provider, provider_payment_id)` на уровне БД |

### 2.5 API-контракт платежей (`app/transport/http/routers/payments.py`)

| Метод | Путь | Auth | Назначение |
|---|---|---|---|
| GET | `/api/subscription` | JWT (Mini App) | статус+лимиты тарифа, `prices` |
| POST | `/api/payments/stars` | JWT | pending + `createInvoiceLink` (Mini App) |
| GET | `/api/users/{id}/subscription` | internal (`X-Internal-Token`) | статус для бота `/subscribe` |
| POST | `/api/payments/prepare` | internal | pending для `sendInvoice` из бота |
| POST | `/api/payments/success` | internal | «вебхук» `successful_payment` → `ConfirmPayment` |

Пример `GET /api/subscription`:

```json
{
  "tier": "pro",
  "paid_tier": "pro",
  "subscription_until": "2026-11-27T00:00:00Z",
  "max_filters": 20,
  "ai_daily_limit": 200,
  "instant": false,
  "prices": {"pro_month": 500, "pro_year": 4800, "business_month": 2500, "business_year": 24000},
  "digest": "daily",
  "inspection": true,
  "agent": false,
  "analyses_limit": 30,
  "analyses_window_days": 1
}
```

Пример `POST /api/payments/stars` → 200:

```json
{
  "payment_id": 123,
  "invoice_url": "https://t.me/$<invoice-hash>",
  "amount": 500,
  "tier": "pro",
  "period_days": 30
}
```

Пример `POST /api/payments/success` (internal):

```json
{
  "payment_id": 123,
  "user_id": 100200300,
  "provider_payment_id": "<telegram_payment_charge_id>"
}
```

Ответ (подписка): `{"activated": true, "tier": "pro", "subscription_until": "<ISO>", "kind": "subscription", "listing_id": null, "placement_paid_until": null}`.
Ответ (размещение): `{"activated": true, "tier": null, "subscription_until": null, "kind": "placement", "listing_id": 456, "placement_paid_until": "<ISO>"}`.

Ошибки: 404 — платёж/пользователь/лот не найдены или платёж чужой;
409 (`Payment is incomplete`) — в pending нет tier/period/listing;
503 — не настроен `bot_token`.

---

## 3. Внутренний рынок: placement-модель и жизненный цикл

Код: `app/application/market/use_cases.py`, `dto.py`, `moderation.py`;
репозиторий `app/infrastructure/db/repositories/market_lots.py`;
миграции 0026 (чат-рынок), **0027** (owner-лоты, payments.kind),
**0030** (lifecycle-статусы).

### 3.1 Поля БД (миграции 0027, 0030)

`listings`:

| Колонка | Тип/значения | Смысл |
|---|---|---|
| `owner_type` | `NULL` \| `user` \| `partner` | внешний лот (encar/kcar/che168) или owner-лот рынка |
| `owner_user_id` / `owner_partner_id` | BigInteger FK | ровно один заполнен (два частичных CHECK `owner_user_filled` / `owner_partner_filled`) |
| `owner_description` | Text ≤2000 | описание владельца |
| `placement_free_until` | timestamptz | конец бесплатного периода |
| `placement_paid_until` | timestamptz | конец оплаченного периода |
| `status` | CHECK `('draft','moderation','accepted','rejected','archived','expired')`, default `'accepted'` | конечный автомат owner-лотов; внешние лоты живут с `accepted` |
| `rejected_reason` | Text | причина отказа (hard-правила авто-модерации или текст админа) |
| `moderation_payload` | JSONB | `{risk_score: int, violations: [{rule, detail, risk}]}` последнего скрининга |
| `pending_patch` | JSONB | версия-на-модерации правки опубликованного лота; при отказе админ дописывает `patch_reject_reason` внутрь JSON |

`partner_accounts` (миграция 0027): `placement_slots`, `placement_slots_used`
(int, default 0). Индексы очередей модерации — частичные
(`status='moderation'`, `pending_patch IS NOT NULL`) + `idx_listings_status`.

### 3.2 Placement-статусы (`placement_status`, dto.py)

| Статус | Когда |
|---|---|
| `none` | draft/moderation/rejected — публикацией ещё не решались |
| `slot` | партнёрский лот (за слот, без дат) |
| `needs_payment` | неактивный лот без дат (доп. лот ждёт первой оплаты) |
| `paid` | `placement_paid_until > now` |
| `free` | `placement_free_until > now` (и paid нет/истёк) |
| `included` | живой лот платного тарифа без дат; либо активный без дат вовсе |
| `expired` | даты были и `max(free, paid) < now`, владелец на free |

Дедлайн размещения = `max(free_until, paid_until)` (`placement_deadline`);
`None` = бессрочный (included/slot).

Правила первого размещения (`decide_placement`, §3 редизайна):

- **free-тариф**: нет активных лотов И не было рынка за
  `market_free_cooldown_days` (30д, защита от ротации «снять/создать») →
  бесплатная неделя (`placement_free_until = now + 7д`, `is_active=True`).
- **платный тариф**: первый лот → `included` (без дат, `free_until=None`).
- иначе → `needs_payment`: `is_active=False`, публикация после оплаты Stars.

Квота платных лотов: `market_user_max_paid_lots = 3` — превышение даёт 409
`market_limit_reached` (текст: «Pro включает 1 объявление, дополнительные
публикуются за 20 Stars/неделя»).

Конфиг (`app/config.py`): `market_free_days=7`, `market_week_stars=20`
(4 недели = `stars*3` = 60★), `market_free_cooldown_days=30`,
`market_user_max_paid_lots=3`, `market_forbidden_words` (словарь
авто-модерации RU+EN: авито/drom/крипта/оружие/казино/инвестиции/скам …),
`market_partner_packages = {"10": 500, "50": 1800, "200": 5000}` (₽).

### 3.3 Статусная машина жизненного цикла (10 §1)

```
             create (draft)                        submit
  ┌──────────▶ draft ────────────────────────────────┐
  │             │  ▲                                 ▼
  │             │  │ PATCH (прямое обновление)   авто-скрининг screen_lot
  │             │  │                                 │
  │             │ rejected ◀─── hard/risk>=80 ───────┤ reject (reason)
  │             │   │  ▲                             │
  │             │   │  │ правка + повторный submit   ├─ manual (risk>=30 /
  │             │   │  └─────────────────────────────┤   new_user / price_outlier)
  │             │   │                                ▼
  │             │   │                          moderation ──▶ админ approve/reject
  │             │   │                                │
  │             │   │                          accept → decide_placement
  │             │   │                                ▼
  │             │   │        PATCH (правка опубликованного)     accepted
  │             │   │            └────────▶ pending_patch ─────▶│
  │             │   │                       (лента видит старое)│
  │             │   │                                │           ▲
  │             │   │                    админ patch_accept/reject
  │             │   │                    (применить / patch_reject_reason)
  │             │   │                                ▼
  │             │   │                        экспиратор 03:40 UTC
  │             │   │                        max(free,paid) < now
  │             │   │                                ▼
  │             │   │  extend (20★/60★, оживляет)  expired
  │             │   │                                │
  └─────────────┴───┴────────────────────────────────┴────▶ archived (DELETE / снятие с продажи)
```

Переходы и guard'ы:

- `CreateMarketLot` (`POST /api/market/lots`): guest → 403; advisory-lock по
  юзеру; проверка квоты; создание `draft` (`is_active=False`, без дат);
  `submit=True` сразу гонит модерацию (совместимость старых клиентов).
- `SubmitLot` (`POST /api/market/lots/{id}/submit`): только из
  `draft`/`rejected` (иначе 409 `lot_invalid_state`); синхронный скрининг
  (3 точечных запроса к БД, без фоновой очереди).
- `UpdateLot` (`PATCH /api/market/lots/{id}`): draft/rejected — прямое
  обновление; **accepted → `pending_patch`** (версия-на-модерации, JSON-safe:
  `Decimal → str`); moderation/archived/expired → 409.
- `CloseMarketLot` (`DELETE`): `archived` + `is_active=False` (статус жизни — в
  `status`, `is_active` сохранён для старых фильтров).
- `ExtendLot`: только `accepted`/`expired`.
- Экспиратор `market_placement_expiry` (cron 03:40 UTC, `send.py`):
  included-лоты платных тарифов не трогает; активный лот без дат у free-владельца
  (даунгрейд с тарифа) получает `free_until = now+7д` однократно;
  `max(free,paid) < now` → `status='expired'` + centrifugo-событие
  `{type:'listing_status', listing_id, status:'expired'}` в `user:{owner_id}`.
- События статусов публикуются в личный канал centrifugo
  (`user:{tg_id}` / `partner:{id}`) — `ListingStatus` экрана «Мои объявления»
  обновляется в реальном времени.

### 3.4 Авто-модерация `screen_lot` (`app/application/market/moderation.py`)

LLM **не** используется — правила дешёвые и предсказуемые. Чистая функция
`screen_lot(lot, is_new_user, similar_avg_price, has_duplicate, forbidden_words,
bot_username)`:

| Правило | Риск | Класс |
|---|---|---|
| `contacts_in_text` — контакт в описании (regex `app/domain/shared/contacts.find_contacts`, t.me нашего бота исключён) | +30 | **hard** |
| `forbidden_words` — запрещённое слово в описании ИЛИ марке/модели (подстрока, без регистра) | +40 | **hard** |
| `price_outlier` — цена < 10% средней той же модели (`PRICE_OUTLIER_FRACTION=0.1`) | +20 | manual |
| `short_description` — описание < 30 симв. | +10 | soft |
| `new_user` — у владельца нет ни одного accepted-лота кроме этого | +25 | manual |
| `duplicate` — тот же owner+make+model+year+price за 7 дней (`DUPLICATE_WINDOW_DAYS`) | +35 | soft |

Вердикт: `risk >= 80` или любое hard-правило → **reject** (в `rejected_reason`
попадают детали hard-нарушений); `risk >= 30` или любое manual-правило →
**manual** (очередь админа); иначе **accept** → placement-правила + публикация.
`needs_payment` **не блокирует** модерацию: лот `accepted`, но скрыт до оплаты.

Партнёрские лоты правилами не проверяются (слоты платные → авто-accept), но
**10% выборки** (`PARTNER_SAMPLING_RATE = 0.1`, `random < 0.1`) уходит в ручную
очередь с причиной `sampling` (контроль качества).

Админ-очереди (`GET /api/admin/moderation?queue=moderation|patch`,
`ListModerationQueue`): `moderation` — лоты на проверке (риск по убыванию),
`patch` — `pending_patch`. Решения:

- `POST /api/admin/moderation/{id}/approve` — placement-правила + `accepted`
  (партнёрский — просто публикация, слот уже оплачен);
- `POST /api/admin/moderation/{id}/reject` — `rejected` + reason;
- `POST /api/admin/moderation/{id}/patch_accept` — применить патч к полям лота
  (photos целиком), очистить `pending_patch`;
- `POST /api/admin/moderation/{id}/patch_reject` — дописать
  `patch_reject_reason` внутрь патча (владелец видит в my-lots).

### 3.5 Партнёрские размещения: пакеты слотов

- `GET /api/partner/market/packages` — каталог из конфига: 10 мест = 500 ₽,
  50 = 1800 ₽, 200 = 5000 ₽ (цены Decimal — баланс `Numeric(12,2)`).
- `POST /api/partner/market/packages {size}` — `BuyPackage`: атомарно в одной
  транзакции `balance -= price`, `slots += size`, партнёрская транзакция
  `kind='adjustment'`, комментарий `placement package {size}`; ответ содержит
  `slots_free` после покупки. Не хватает баланса → 402 `insufficient_balance`.
- `POST /api/partner/market/lots` — `CreatePartnerLot`: атомарное
  `consume_placement_slot` (условный `UPDATE … WHERE slots-used > 0`,
  `rowcount==0` → 409 `placement_slots_exhausted`); лот создаётся сразу
  `accepted`/`is_active=True` (без placement-дат — слот бессрочный); сэмплинг
  10% может перевести в `moderation` (ответ всё равно 201, статус в поле).
- `DELETE /api/partner/market/lots/{id}` — снять размещение, слот **не
  возвращается**.

### 3.6 API рынка (сокращённо, `app/transport/http/routers/market.py`)

| Метод | Путь | Смысл |
|---|---|---|
| GET | `/api/market/offers` | витрина-лента с фильтрами (make, price_max) |
| POST | `/api/market/lots` | создать draft (`{submit: bool, make, model, year, price, currency, mileage_km, fuel, transmission, description, photos[]}`) |
| POST | `/api/market/lots/{id}/submit` | отправить на публикацию (модерация) |
| PATCH | `/api/market/lots/{id}` | правка / версия-на-модерации |
| GET | `/api/market/my-lots` | мои объявления: placement-статус, дедлайны, `violations`, `pending_patch`, `quality` |
| DELETE | `/api/market/lots/{id}` | снять с продажи (archived) |
| POST | `/api/market/lots/{id}/extend` | инвойс Stars на 1/4 недели (kind='placement') |

Пример ответа `POST /api/market/lots/{id}/submit`:

```json
{
  "listing_id": 456,
  "status": "accepted",
  "placement_status": "needs_payment",
  "is_active": false,
  "needs_payment": true,
  "rejected_reason": null,
  "violations": [{"rule": "short_description", "detail": "описание короче 30 символов", "risk": 10}]
}
```

---

## 4. Партнёрская экономика: CPL-лиды, баланс, авто-Business

Код: `app/application/partners/use_cases.py` (ClaimLead/ClaimBatch/TopUp),
`app/domain/partners/pricing.py` (чистые правила),
`app/infrastructure/services/partners/repository.py` (SQL),
`app/application/partners/queries.py` (billing/отчёты),
`app/application/partners/market.py` (рыночная аналитика).

### 4.1 Планы и CPL-ставки

`PLAN_CATALOG` (`domain/partners/pricing.py`):

| План | Дневная квота | CPL (USD за лид) |
|---|---|---|
| starter | 10 | 5.00 |
| pro | 50 | 3.00 |
| salon | 200 | 2.00 |

`cpl_cost(plan, cpl_rate)`: индивидуальный `cpl_rate` партнёра перекрывает план;
неизвестный план логируется и считается по starter (claim не должен падать из-за
опечатки). Конфиг-оверрайд `PARTNER_CPL_RATES` (env, JSON).

`can_claim(balance, cost, quota, used, n)`: **сначала квота, потом баланс**
(порядок причин отказа важен): `used + n > quota` → `quota_exceeded`;
`balance < cost*n` → `insufficient_balance`. Квота и баланс проверяются на весь
запрошенный объём — недобор не исполняется «частично».

### 4.2 Claim одного лида (пошагово)

`POST /api/partner/leads/{lead_id}/claim` (auth: `X-API-Key` партнёра):

1. `get_account(partner_id)` — счёт (suspend-партнёр получает 401 на уровне
   ключа: правило «ключ active И аккаунт active»).
2. `cpl_cost` → `can_claim` на 1 лид. При пустом балансе — авто-**suspend**
   счёта + алерт админу (`partner_suspended:{id}`, dedup).
3. Атомарный claim: `UPDATE leads SET partner_id=:p, partner_status='claimed',
   claimed_at=now() WHERE id=:id AND partner_id IS NULL RETURNING *`.
   `rowcount=0` → различить `not_found` / `already_yours` / `taken`.
4. Списание: `update_balance_returning(partner_id, -unit_cost)` —
   `UPDATE … SET balance = balance + :delta RETURNING balance` (состояние
   читается из RETURNING, не отдельным SELECT — нет гонки чтения).
5. Журнал: `partner_transactions (kind='charge_lead', amount=-unit_cost,
   balance_after)`; частичный уникальный индекс `uq_partner_tx_lead_kind
   (lead_id, kind) WHERE lead_id IS NOT NULL` — повторное списание за тот же
   лид невозможно на уровне БД.
6. Если рядом с лидом есть deal-тред (заявка на агента) — тред привязывается к
   счёту и в него пишется system-сообщение «Агент {name} подключился» (сбой
   чатов не отменяет claim — весь блок в try/except).
7. `commit` одной транзакцией; событие аналитики `lead_sold`.

`ClaimBatch` (`POST /api/partner/leads/batch {limit}`): кандидаты
`SELECT id FROM leads WHERE partner_id IS NULL ORDER BY created_at LIMIT :lim
FOR UPDATE SKIP LOCKED` — параллельные партнёры не блокируют друг друга и не
забирают одни и те же строки; затем условный `UPDATE … WHERE id IN (…) AND
partner_id IS NULL RETURNING`, списание `unit_cost * len(leads)` одной строкой
баланса, журнал на каждый лид. Пустой пул — не ошибка.

`TopUp` (`POST /api/admin/partners/{id}/topup`, internal):
`update_balance_returning(+amount, status=...)` + транзакция `kind='topup'`
(или `'adjustment'`); **авто-разбан**: пополнение, выводящее баланс в плюс,
возвращает `status='active'` (`should_reactivate`: `kind=='topup' and
balance > 0 and status=='suspended'`); корректировки статус не меняют.
Audit-запись `partner.topup` — best-effort после коммита.

### 4.3 Биллинг-отчёт и рыночная аналитика

- `GET /api/partner/billing` — транзакции счёта (`kind IN ('topup',
  'charge_lead', 'adjustment')`, CHECK в БД, миграция 0014) + агрегаты.
- `GET /api/partner/market?make=&model=` — сводка рынка (count/min/max/median)
  и «выгодные» лоты ниже медианы (инструмент сотрудников партнёра).

### 4.4 Авто-Business владельцу счёта (миграция 0024)

`partner_accounts.owner_telegram_id` (unique) — владелец счёта:

- создание счёта с владельцем / правка владельца/статуса при
  `status='active'` → `grant_partner_business(user)` (бессрочный Business,
  `subscription_until IS NULL`);
- `status='suspended'` → `subscription_tier='free'`, `subscription_until=None`;
- `subscription_check_job` бессрочный Business не трогает (выборка только по
  `subscription_until IS NOT NULL`);
- владелец обязан существовать (иначе `InvalidInput` — счёт не создаётся).

### 4.5 Появление лидов в пуле

- `POST /api/deals/{listing_id}/contact` — заявка «свяжитесь со мной»
  (дубликат в течение суток → 409 `duplicate_contact`);
- `POST /api/deals/{listing_id}/agent` — заявка на агента (**только Business**,
  фич `agent`), создаёт deal-тред;
- фидбек партнёра: `POST /api/partner/leads/{lead_id}/feedback`.

### 4.6 Таблицы партнёрского контура

| Таблица | Миграция | Ключевые поля |
|---|---|---|
| `partner_accounts` | 0006, 0014, 0024, 0027 | plan, daily_quota, status(active/suspended), balance Numeric(12,2), cpl_rate Numeric(10,2) NULL, owner_telegram_id unique, placement_slots, placement_slots_used |
| `partner_transactions` | 0014 | partner_id, lead_id, amount (+/−), kind CHECK ('topup','charge_lead','adjustment'), balance_after, comment; частичный UNIQUE (lead_id, kind) |
| `partner_api_keys` | 0006 | key_hash, key_prefix, status ('active','rotated','revoked'), rotated_at, last_used_at |
| `leads` | 0006 (+0025 idempotency) | partner_id NULL (пул), partner_status, claimed_at, qualification JSONB |

Rate-limit партнёрского API: `partner_rate_limit_per_min = 60` на ключ
(`services/partners/rate_limit.py`, Redis).

---

## 5. Фоновые задания монетизации (`app/infrastructure/workers/send.py`)

| Cron (UTC) | Задача | Что делает |
|---|---|---|
| 09:00 | `digest_job_morning` | дайджест всем тарифам; free — только по понедельникам (`_digest_due`: `weekday()==0`), буфер не трогается до «своего» слота |
| 20:00 | `digest_job_evening` | pro/business (вечерняя подборка) |
| 12:00 | `subscription_check_job` | напоминания за 3/1 день (`sub:reminded:{uid}:{days}`, TTL 2д, антидубль); истёкшим ≤3д — даунгрейд на free + сообщение; давно истёкших (>3д) не спамим; деактивированным (is_active=False) не пишем; бессрочный Business не выбирается |
| 03:40 | `market_placement_expiry` | экспирация/даунгрейд included-размещений (см. §3.3), события `listing_status` |
| 04:30 | `favorites_sold_job` | проданные машины из избранного (смежный контур удержания) |

Даунгрейд подписки: `subscription_tier='free'`, `subscription_until=None`
(TECHDEBT 9.3 — иначе пользователь вечно попадает в выборку), ключи напоминаний
чищутся.

---

## 6. Чек-лист инвариантов (что проверять при изменениях)

1. Сумма платежа считается один раз — в `CreatePendingPayment`/`ExtendLot.price_for`;
   транспорт и фронт только отображают.
2. `mark_paid` — всегда условный UPDATE по `status != 'paid'`; эффект
   (подписка/дедлайн) применяется только при `rowcount == 1`.
3. Платёж + эффект — одна транзакция, один commit.
4. `pre_checkout` whitelist — ровно `sub:<digits>` / `plc:<digits>`.
5. Free-период рынка — не чаще раза в 30 дней на юзера; параллельные
   create/submit сериализуются `pg_advisory_xact_lock(9106, user_id)`.
6. Партнёрский слот сгорает при публикации и не возвращается; списание за
   лид — один раз (частичный UNIQUE в журнале).
7. Истёкшая подписка = free в `effective_tier` везде, где читается тариф
   (анализы, дайджесты, placement, чаты поддержки).
8. Партнёрский `sampling` не меняет экономику: 201/`accepted`, просто
   статус в ответе.
