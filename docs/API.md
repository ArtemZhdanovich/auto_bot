# HTTP API

Приложение: `app/main_api.py` (mono) или доменные сервисы `services/*`
(micro) — OpenAPI на `/docs`. Роутеры — `app/transport/http/routers/`.

## Аутентификация — три схемы

| Схема | Заголовок | Кто | Что защищает |
|---|---|---|---|
| JWT (Telegram initData или web-логин) | `Authorization: Bearer` | юзеры Mini App | users/me/filters/listings/favorites/payments |
| Internal Token | `X-Internal-Token` | админка/крон/бот | `/api/admin/*`, `/metrics`, `/api/dialog/*`, `/api/notifications/*`, `/api/leads/*` |
| Partner API Key | `X-API-Key` | партнёры (CPL) | `/api/partner/*` |

Внутренний токен сравнивается через `hmac.compare_digest`
(`app/transport/http/deps/internal.py`); защищённые роуты монтируются через
`InternalDishkaRoute` — проверка до dishka-резолва сценариев.

## Пользователи и профили (identity)

- `POST /api/auth/telegram` — валидация initData (HMAC, TTL 24 ч) → JWT.
- `POST /api/auth/register`, `POST /api/auth/login`, `POST /api/auth/refresh`
  — веб-авторизация (access 1 ч / refresh 30 дней);
  `POST /api/auth/magic-link` — заглушка (501).
- `GET /api/me` — профиль; `POST /api/me/onboarded` — флаг онбординга;
  `POST /api/users/upsert` (internal) — создание/обновление пользователя ботом.

## Фильтры и лента (catalog)

- `GET/POST/PATCH/DELETE /api/filters[/{id}]` — CRUD, лимит по тарифу
  (Free 3 / Pro 20 / Business ∞), criteria валидируется `SearchCriteria`.
- `GET /api/listings` — лента с фильтрами make/model/year/price/mileage/source,
  пагинация `limit/offset` (≤50), новые сверху.
- `GET /api/listings/{id}` — карточка + история цен (`listing_price_history`).
- `GET/POST/DELETE /api/favorites[...]` — избранное; отдаёт `first_price`
  (бейдж «⬇️ цена снизилась») и `is_active=false` (бейдж «🔴 исчезло»).

## Диалог и AI-поиск (dialog)

- `POST /api/dialog/message` (internal) — ход диалога: юзер → агент → ответ.
- `POST /api/ai/message` (JWT) — тот же агент из Mini App (AI-подбор авто);
  `POST /api/ai/reset`, `POST /api/ai/finalize` — сброс/финализация критериев.

## Уведомления (catalog)

- `POST /api/notifications/settings` (internal) — instant/digest, тихие часы
  (вызывает бот: /stop //resume и меню «Уведомления»).
- `POST /api/notifications/scan` (internal) — принудительное сканирование.

## Лиды (воронка диалог → обращение)

- `POST /api/leads` (internal) — создать лид (бот/агент после квалификации);
  событие `lead_created`.
- `PATCH /api/leads/{id}` — статус менеджера: new/in_progress/closed/rejected.

## Монетизация (billing)

- `GET /api/subscription` — статус, лимиты, цены.
- `POST /api/payments/stars` — инвойс (XTR) + `createInvoiceLink`.
- `GET /api/payments/history` — история платежей.

## Админка (`/api/admin`, X-Internal-Token)

- `GET /api/admin/adapters` — зарегистрированные адаптеры.
- `GET /api/admin/sources`, `PUT /api/admin/sources/{name}` — конфиг источника
  в БД (base_url, page_size, proxy и т.д. — без деплоя); URL валидируется на
  SSRF (только http/https, запрет приватных подсетей).
- `GET /api/admin/sources/{name}/health` — latency/ошибки/CB.
- `POST /api/admin/sources/{name}/fetch?max_pages=N` — ручной запуск парсинга.
- `GET /api/admin/leads` — список лидов со статусами.
- `POST /api/admin/assistant/chat` — AI-ассистент админки.
- `GET /api/admin/listings/{id}/raw` — presigned GET сырого JSON из MinIO
  (10 мин; `raw_key IS NULL` → 404, S3 недоступен → 502).
- `GET /api/admin/analytics/funnel?days=30` — воронка
  search→dialog→lead→subscription.
- `GET /api/admin/analytics/retention?days=30` — когорты по дню регистрации,
  D1/D7.

### Партнёры — админ (`/api/admin/partners`)

- `POST /api/admin/partners` — создать партнёра + выдать ключ (сырой ключ
  показывается **один раз**, хранится только SHA-256).
- `GET /api/admin/partners` — список со статистикой (claimed_today, баланс).
- `POST .../{id}/rotate` — ротация ключа (старый мгновенно инвалиден);
  `POST .../{id}/revoke` — отзыв всех ключей.
- `PATCH .../{id}` — план/квота/статус/cpl_rate (`reset_cpl_rate` → тариф
  плана).
- `POST .../{id}/billing` — пополнение баланса (>0, снимает suspended) или
  корректировка (<0); пишется в `partner_transactions`.

### Партнёры — API (`/api/partner`, X-API-Key)

- `GET /api/partner/me` — аккаунт: квота, баланс, цена следующего лида.
- `GET /api/partner/leads` — пул доступных лидов (ПД скрыты до claim);
  `?mine=1` — свои лиды (с ПД).
- `POST /api/partner/leads/batch` — «взять N» (квота **и** баланс).
- `POST /api/partner/leads/{id}/claim` — эксклюзивный claim (409 taken,
  429 квота, **402 нет денег**).
- `POST /api/partner/leads/{id}/feedback` — качество лида:
  claimed/accepted/returned/junk/deal.
- `GET /api/partner/billing` — план, баланс, каталог тарифов.

## Системные

- `GET /health` — liveness.
- `GET /health/detailed` — postgres/redis/rabbitmq; 503 если лежит БД (в micro
  — агрегат пингов всех доменных сервисов).
- `GET /metrics` (internal) — юзеры/новые 24ч, тарифы, объявления, отправленные
  уведомления, health источников, события воронки за 24 ч.
