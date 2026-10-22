# Монетизация: подписки, лиды, партнёры

## Подписки и Telegram Stars (billing)

- Тарифы Free / Pro / Business: лимиты фильтров (3 / 20 / ∞) и AI-сообщений;
  доменная логика — `app/domain/limits`, сценарии — `app/application/{subscription,payments}`,
  контракты — `app/application/billing/{ports,dto}.py`.
- Оплата — только Telegram Stars (XTR): деньги на аккаунт бота у Telegram;
  `pre_checkout_query` валидируется, `successful_payment` активирует тариф
  (идемпотентно по charge id). Сторонних платёжных ссылок/кошельков нет.
- Инвойс — `POST /api/payments/stars` (+ `createInvoiceLink`), история —
  `GET /api/payments/history`; бот: `/subscribe`, колбэки `sub:buy:<tier>`.
- `subscription_check_job` (12:00 UTC) — истечение подписок.

## Лиды (воронка диалог → обращение)

Диалог/агент после AI-квалификации (бюджет/срок/предоплата) создаёт лид
(`POST /api/leads`, internal) → менеджер ведёт статусы
(`PATCH /api/leads/{id}`: new/in_progress/closed/rejected). Квалификация
сохраняется в `Lead.qualification` — квалифицированные лиды попадают в
партнёрский пул.

## Партнёрский контур с CPL-биллингом (partners)

ИП/перекупы/салоны покупают лиды (CPL). Контракты —
`app/application/partners/{ports,dto}.py`; сценарии — `use_cases.py`
(claim/topup/feedback) и `queries.py` (пул/мои лиды).

- Доступ по `X-API-Key` (`pi_…`, хранится только SHA-256; ротация мгновенно
  инвалидирует старый ключ). Rate limit 60 req/min на ключ (fixed window
  Redis, `PARTNER_RATE_LIMIT_PER_MIN`).
- Планы: `starter` / `pro` / `salon` — дневная квота лидов и CPL-тариф
  (5 / 3 / 2 USD; `PARTNER_CPL_RATES`). Индивидуальный `cpl_rate` партнёра
  перекрывает тариф плана (`reset_cpl_rate` возвращает план).
- **Claim** — эксклюзивный: `partner_id` ставится атомарно
  (`UPDATE ... RETURNING` / `SKIP LOCKED`), ПД (`user_id`) отдаются только
  после claim; пул — без ПД. Batch-claim учитывает квоту **и** баланс
  (`affordable = balance // cost`).
- **Биллинг** (миграция `0014`):
  - при claim списывается цена лида; транзакция `charge_lead` пишется в
    `partner_transactions` с `balance_after` (аудит-трейл);
  - баланс < цены → claim отклонён (**402**), партнёр автоматически
    `suspended` + алерт админу (дедуп 24 ч);
  - пополнение `POST /api/admin/partners/{id}/billing` (amount > 0) снимает
    suspended и пишет `topup`; отрицательный amount — ручная корректировка
    (`adjustment`), suspended не снимает.
- **Фидбек** партнёра по качеству лида (`accepted/deal` vs `junk/returned`);
  события `partner_lead_claimed`, `partner_lead_feedback` — в
  `analytics_events`.

## Аналитика и воронки (admin)

`analytics_events` (миграция 0004) — только факты воронки, без ПД в props:
`user_registered`, `search_completed`, `dialog_message`, `lead_created`,
`partner_lead_claimed`, `partner_lead_feedback`, `subscription_activated`.
`track()` никогда не ломает бизнес-поток (ошибка → warning).

Дашборды (read-only SQL):
- `GET /api/admin/analytics/funnel?days=N` — distinct-юзеры на каждом этапе
  search→dialog→lead→subscription, конверсии к предыдущему (`conv_prev_pct`)
  и к первому (`conv_first_pct`);
- `GET /api/admin/analytics/retention?days=N` — когорты по дню
  `user_registered`: размер когорты, D1/D7 абсолютные и в %.

Быстрые агрегаты за 24 ч — в `GET /metrics` (`events_24h`).
