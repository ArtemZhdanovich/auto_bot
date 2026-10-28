# HTTP API — полный справочник

Справочник по всем HTTP-эндпоинтам проекта: монолит `app/main_api.py` (профиль
`mono`), микросервисы `services/*` за gateway (профиль `micro`) и вспомогательные
сервисы (RAG, translate). Каждый эндпоинт описан по единому шаблону: назначение,
авторизация, параметры, ответ, ошибки, лимиты/идемпотентность, пример curl.

Источники истины: роутеры `app/transport/http/routers/*.py`, схемы
`app/transport/http/schemas/*.py`, таблица маршрутов
`services/gateway/routing.py`, конфиг `configs/centrifugo.json.tmpl`.
OpenAPI-схема каждого приложения — `GET /docs` (Swagger UI).

---

## Оглавление

1. [Базовые URL и режимы развёртывания](#базовые-url-и-режимы-развёртывания)
2. [Версионирование](#версионирование)
3. [Авторизация](#авторизация)
4. [Формат ошибок](#формат-ошибок)
5. [Пагинация и сортировка](#пагинация-и-сортировка)
6. [WebSocket-каналы Centrifugo](#websocket-каналы-centrifugo)
7. [Системные эндпоинты (health, metrics)](#системные-эндпоинты)
8. [Auth: вход, регистрация, гости, refresh](#auth-вход-регистрация-гости-refresh)
9. [Профиль: /api/me, /api/users/upsert](#профиль)
10. [Лента и карточки: /api/listings (публичные)](#лента-и-карточки)
11. [Избранное: /api/favorites](#избранное)
12. [Фильтры-подписки: /api/filters](#фильтры-подписки)
13. [AI-анализ цены и похожие: /api/listings/{id}/analysis|similar|history-report](#ai-анализ-цены)
14. [Сделки: agent / contact](#сделки)
15. [Калькулятор: /api/calc](#калькулятор)
16. [AI-поиск (web): /api/ai](#ai-поиск-web)
17. [Диалог бота (internal): /api/dialog](#диалог-бота-internal)
18. [Чаты (JWT): /api/chats](#чаты-jwt)
19. [Внутренний рынок (JWT): /api/market](#внутренний-рынок-jwt)
20. [Подписки и платежи: /api/subscription, /api/payments](#подписки-и-платежи)
21. [Партнёрский API (X-API-Key): /api/partner](#партнёрский-api)
22. [Лиды (internal): /api/leads](#лиды-internal)
23. [Уведомления (internal): /api/notifications](#уведомления-internal)
24. [Admin (X-Internal-Token): /api/admin, /metrics](#admin)
25. [Вспомогательные сервисы: RAG, translate](#вспомогательные-сервисы)
26. [Приложение: справочник machine-кодов ошибок](#справочник-machine-кодов)

---

## Базовые URL и режимы развёртывания

Два взаимоисключающих профиля `docker compose` (общая инфраструктура:
postgres 5432, redis 6379, rabbitmq 5672/15672, qdrant 6333, minio 9000/9001):

| Режим | Точка входа API | Состав |
|---|---|---|
| `--profile mono` | `http://localhost:8000` | монолит `app/main_api:app` — все роутеры в одном процессе |
| `--profile micro` | `http://localhost:8000` | gateway `services/gateway/main.py` (сетевой алиас `backend`) + доменные сервисы |

Профиль `micro`: gateway маршрутизирует `/api/*` в сервисы (порты хоста):

| Сервис | Порт | Домены (по `services/gateway/routing.py`) |
|---|---|---|
| identity | 8101 | `/api/auth`, `/api/me`, `/api/users` |
| catalog | 8102 | `/api/listings`, `/api/favorites`, `/api/filters`, `/api/notifications`, `/api/calc`, `/api/bot` |
| billing | 8103 | `/api/subscription`, `/api/payments`, `/api/users/{id}/subscription` |
| partners | 8104 | `/api/partner` (кроме mirrors), `/api/admin/partners`, `/api/leads` |
| dialog | 8105 | `/api/dialog`, `/api/ai` |
| admin | 8106 | `/api/admin/*` (кроме partners/chats/moderation — см. ниже), `/metrics` |
| chat | 8107 | `/api/chats`, `/api/market`, `/api/partner/offers`, `/api/partner/chats`, `/api/partner/market`, `/api/partner/listings` |

Правила применяются «первый совпавший префикс побеждает» — специфичные правила
партнёрских зеркал (`/api/partner/offers|chats|market|listings` → chat) стоят
раньше общего `/api/partner` → partners. Gateway пробрасывает метод, query,
тело и все заголовки кроме hop-by-hop (включая `Authorization`, `X-API-Key`,
`X-Internal-Token`) — аутентификация выполняется в доменном сервисе. Неизвестный
маршрут → 404 `{"detail": "no route for <path>"}`; недоступный upstream → 502
`{"detail": "service '<name>' unavailable"}`.

Прочие точки:

- Centrifugo (ws/realtime): хост-порт `8008` (внутри сети `:8000`);
- RAG API: `http://localhost:8100` (профиль `ai`/`micro`);
- translate API: `http://localhost:8200`;
- Mini App (nginx): `http://localhost:8080` / `https://localhost:8443`.

Во всех примерах ниже база — `http://localhost:8000` (монолит или gateway —
клиенту безразлично, контракт идентичен).

## Версионирование

Версионирования API **нет** — это зафиксированное решение. Пути не содержат
префикса `/v1`; контракт меняется только обратно-совместимо (аддитивные поля:
например, keyset-поля `next_cursor`/`has_more` добавлены в `ListingListOut`
без ломки старых клиентов). Единственное исключение — вспомогательный RAG-сервис,
у которого путь версионирован (`/api/v1/ask`), но он внутренний. При необходимости
ломающих изменений — новый документ/миграция клиентов, префикс версии вводится
отдельным решением.

## Авторизация

Три контура защиты; каждый роутер целиком принадлежит одному контуру.

### 1. JWT гостя/юзера (`Authorization: Bearer <access_token>`)

Выдача — POST-ручки `/api/auth/*` (см. [Auth](#auth-вход-регистрация-гости-refresh)):

| Поток | Ручки | Кто |
|---|---|---|
| Mini App (Telegram initData, HMAC-проверка по `BOT_TOKEN`) | `POST /api/auth/telegram` | юзер Telegram |
| Веб-регистрация / вход (PBKDF2-SHA256) | `POST /api/auth/register`, `POST /api/auth/login` | юзер сайта |
| Гостевая сессия (демо без регистрации) | `POST /api/auth/guest` | гость |
| Обновление пары токенов | `POST /api/auth/refresh` | все |
| Вход через бота (код → `/start login_<code>`) | `POST /api/auth/telegram/link` + `GET /api/auth/telegram/link/{code}` | юзер сайта |

Пара токенов: `access_token` (HS256, TTL `ACCESS_TOKEN_TTL_SEC`, по умолчанию
3600с) + `refresh_token` (TTL `REFRESH_TOKEN_TTL_SEC`, 30 дней). Ротации refresh
нет (осознанный TECHDEBT): повторный `POST /refresh` со старым refresh валиден,
пока не истёк. Ответ всех auth-ручек содержит и пару, и профиль
(`AuthResponse`), refresh-ручки — только пару (`TokenPair`).

**Гость** — синтетический пользователь с `telegram_id < 0`, без email/пароля.
Гостю доступны: лента, фильтры ленты, карточка, калькулятор, избранное.
Закрыто гостю (403 с machine-кодом): чаты целиком (`chats_registration_required`),
AI-анализ цены (`guest_forbidden`), AI-сообщения, размещение лотов, загрузка
фото, ws-токен, заявки contact. Гость видит кнопку «Войти/Зарегистрироваться»
для апгрейда сессии.

Проверка Bearer выполняется dishka-провайдером до эндпоинта: битый/просроченный
токен → 401; деактивированный пользователь → 403.

### 2. Партнёрский API-ключ (`X-API-Key: pi_...`)

Ключ формата `pi_` + 32 base64url-символа (`app/infrastructure/security/partner_keys.py`),
в БД хранится только SHA-256. Raw-ключ отдаётся **один раз** при создании
партнёра (`POST /api/admin/partners`) или ротации (`.../rotate`). Заголовок
`X-API-Key` валидируется на каждом запросе: ключ active **и** аккаунт active,
иначе 401. Окно запросов: `partner_rate_limit_per_min` (по умолчанию 60/мин,
Redis) — превышение 429 `Rate limit exceeded...`.

### 3. Внутренний токен (`X-Internal-Token: <INTERNAL_TOKEN>`)

Бот, воркеры, админ-панель и centrifugo-proxy. Проверяется в route-классе
`InternalDishkaRoute` до выполнения эндпоинта — «забыть» проверку на новом
маршруте нельзя. Значение: `INTERNAL_TOKEN` из `.env`, при пустом — fallback
`APP_SECRET_KEY`; оба пусты → 503 (fail-closed). Ошибки: отсутствует/неверен →
401 `Invalid internal token` (+ `WWW-Authenticate: X-Internal-Token`).

Исключение: `POST /api/chats/subscribe-proxy` принимает `X-Internal-Token` =
`INTERNAL_TOKEN`/`APP_SECRET_KEY` **или** `CENTRIFUGO_HTTP_API_KEY` (centrifugo
проксирует статический api-ключ из конфига).

### Формат ошибок

Единый формат FastAPI: `{"detail": "<текст>"}`; ошибки валидации схем —
`{"detail": [ {loc, msg, type}, ... ] }` со статусом 422. В `detail` прикладных
ошибок первым словом идёт machine-код (`chats_registration_required: ...`,
`duplicate_contact: ...`) — полный список в [Справочнике machine-кодов](#справочник-machine-кодов).

Статус-мэппинг типовых прикладных кодов (единый по роутерам):
`not_found → 404`, `forbidden → 403`, `conflict → 409`, `invalid_input → 400/422`,
`payment_required → 402`, `file_too_large → 413`, `unsupported_media_type → 415`,
`photos_unavailable → 503`.

### Пагинация и сортировка

Три паттерна (описаны здесь, далее по тексту не повторяются):

1. **Offset** (списки лидов, фильтров, facets): `limit` (дефолт зависит от ручки,
   всегда `ge=1` и с потолком) + `offset` (`ge=0`). Стабильность порядка при
   листании не гарантируется.
2. **Keyset-курсор** (лента `GET /api/listings` с `sort=new`, избранное):
   ответ содержит `next_cursor`; следующий запрос передаёт его в `cursor`.
   В режиме курсора `total` = 0 (`count(*)` не выполняется). Битый курсор →
   400 `Invalid cursor`. Курсор ленты поддерживается **только** с `sort=new`
   (иначе 422 `cursor is supported only with sort=new`).
3. **Сообщение-пагинация чатов**: `before_id` (id < X, листание истории вниз)
   и `after_id` (id > X, long-poll инкремента), взаимоисключающие; сортировка
   всегда по возрастанию id.

Сортировки ленты (`sort`): `new` (дефолт), `price_asc`, `price_desc`,
`mileage_asc`, `year_desc`.

### WebSocket-каналы Centrifugo

Сервер realtime — Centrifugo v6 (`configs/centrifugo.json.tmpl`, образ
`centrifugo/centrifugo:v6`, хост-порт 8008). Конфигурация namespaces
(`user`, `partner`, `thread` — у всех `presence: true`, `subscribe_proxy_enabled:
true`) берётся из шаблона; секреты (`CENTRIFUGO_SECRET`, `CENTRIFUGO_HTTP_API_KEY`,
`INTERNAL_TOKEN`) подставляются env-переменными при старте контейнера.

Подключение браузера: `GET /api/chats/ws-token` (или partner-зеркало) →
`{token, url}`; connection-JWT HS256 с `sub` = `user:{telegram_id}` или
`partner:{id}`, TTL 12 часов. Каналы:

| Канал | Кто может подписаться (проверка в subscribe-proxy) |
|---|---|
| `user:{telegram_id}` | только сам юзер (`sub == channel`) |
| `partner:{id}` | только сам партнёр |
| `thread:{uuid}` | только участники треда (user/partner по строке chat_participants) |

События (payload `data`, публикуются сервером в личные каналы участников; у
каждого события есть `thread_id`):

- `{"type": "message", thread_id, id, sender_type, sender_id, body, created_at}` —
  новое сообщение (REST POST messages и операторский ответ);
- `{"type": "read", thread_id, ...}` — read-маркер участника;
- `{"type": "typing", thread_id, user_type}` — «печатает» (эфемерное, не
  доставляется самому отправителю);
- `{"type": "listing_status", listing_id, status, reason?}` — вердикт модерации
  лота владельцу (accepted/rejected/…);
- `presence` — онлайн-статусы через presence-API личных каналов
  (`GET /api/chats/online?ids=`).

Публикация fire-and-forget: сбой centrifugo не ломает REST — клиент доезжает
поллингом `GET /api/chats/{thread_id}/messages?after_id=`. Если centrifugo не
настроен (dev-заглушка ключа), online-ручки честно возвращают `{"online": []}`.

---

## Системные эндпоинты

### GET /health

Liveness. Без авторизации. Монолит: `{"status": "ok", "env": "<app_env>"}`.
Gateway и каждый доменный сервис добавляют `"service": "<имя>"`. 200 всегда,
когда процесс жив.

### GET /health/detailed (монолит и gateway)

Зависимости. Без авторизации. Монолит проверяет postgres/redis/rabbitmq;
gateway пингует все семь доменных сервисов. 200 если все критичные компоненты
живы, иначе 503. Тело:

```json
{
  "status": "ok",
  "env": "dev",
  "components": {"postgres": true, "redis": true, "rabbitmq": false}
}
```

### GET /metrics

Auth: **X-Internal-Token** (роутер `metrics.py`, поднят и в монолите, и в
admin-сервисе; gateway проксирует `/metrics` в admin). Базовые метрики
мониторинга: пользователи (total/new_24h/by_tier), фильтры, объявления, лиды,
AI-разборы, уведомления за 24ч, здоровье источников, события за 24ч. Кэш Redis
30с (Prometheus скрэйпит каждые 15с). БД недоступна → 503
`Database is unavailable`. Ответ — `MetricsOut` (см. роутер `metrics.py`).

```bash
curl -H "X-Internal-Token: $INTERNAL_TOKEN" http://localhost:8000/metrics
```

---

## Auth: вход, регистрация, гости, refresh

Роутер `app/transport/http/routers/auth.py`, префикс `/api/auth`. Все ручки,
кроме отмеченных «internal», без авторизации. Общий ответ — `AuthResponse`:

```json
{
  "access_token": "<JWT>",
  "refresh_token": "<JWT>",
  "token_type": "bearer",
  "user": {
    "telegram_id": 123456789,
    "username": null, "first_name": "Иван", "last_name": null,
    "language_code": "ru",
    "role": "buyer",
    "subscription_tier": "free",
    "subscription_until": null,
    "created_at": "2026-01-01T00:00:00Z",
    "is_active": true,
    "onboarded_at": null
  }
}
```

### POST /api/auth/telegram

Вход по initData из Mini App → JWT-пара + профиль. Body: `{"init_data": "<строка initData>"}`.
Ошибки: 503 — `BOT_TOKEN` не сконфигурирован; 401 — невалидная подпись initData
(`InvalidInitData`); 403 — пользователь деактивирован.

```bash
curl -X POST http://localhost:8000/api/auth/telegram \
  -H "Content-Type: application/json" \
  -d '{"init_data": "query_id=...&user=...&auth_date=...&hash=..."}'
```

### POST /api/auth/register — 201

Регистрация на сайте. Body: `email: str`, `password: str (8..128)`,
`first_name?: str (≤64)`. Ошибки: 422 — неверный email; 409 — email занят;
500 — не удалось выделить user_id. Успех — 201 + `AuthResponse`.

### POST /api/auth/login

Вход на сайте. Body: `email: str`, `password: str (1..128)`. Тайминг-выровнен:
PBKDF2 (~100ms) выполняется всегда, «email нет» неотличим от «пароль неверен».
Ошибки: 422 — email; 401 — неверная пара; 403 — деактивирован.

### POST /api/auth/guest — 201

Гостевая сессия (без body). Создаёт временного пользователя («Гость»,
синтетический `telegram_id < 0`) и сразу выдаёт JWT. Лимит: не более
`GUEST_IP_RATE_LIMIT` (5 по умолчанию) созданий в минуту с одного IP
(X-Forwarded-For, иначе client host) — превышение 429
`Too many guest sessions from this IP, try again later`.

```bash
curl -X POST http://localhost:8000/api/auth/guest
```

### POST /api/auth/refresh

Обмен refresh на новую пару. Body: `{"refresh_token": "<JWT>"}` (`min_length=10`).
Ошибки: 401 — токен не refresh/невалиден или пользователя нет. Ротации нет.

### POST /api/auth/magic-link — 501

Заглушка (ТЗ п. 14.3, этапы 3–4). Body: `{"token": "<str>"}`. Всегда 501
`{"detail": "magic-link auth will be implemented at stage 3-4"}`.

### POST /api/auth/telegram/link

Одноразовый код входа через бота + state-cookie. Без body. Требует
`BOT_USERNAME` (иначе 503). Ответ:

```json
{"code": "Ab12_cdEf", "url": "https://t.me/<bot>?start=login_<code>", "expires_in": 600}
```

Ставит HttpOnly-cookie `tglogin_state` (path `/api/auth/telegram/link`,
SameSite lax/none по конфигу) — код бесполезен в чужом браузере.

### GET /api/auth/telegram/link/{code}

Поллинг статуса (path: код 8..128, `[A-Za-z0-9_-]+`). Требует cookie
`tglogin_state` с этого же браузера: нет/не совпал → 401. Ответы:
`{"status": "pending"}` / `{"status": "expired"}` либо полный `AuthResponse`
(одноразово). 403 — пользователь деактивирован.

### GET /api/auth/telegram/link/{code}/context — internal

X-Internal-Token. Контекст инициатора для бота (анти session fixation):
`{"code", "status", "initiator_ip", "initiator_ua", "created_at"}`. 404 — код
истёк/не найден. PII (IP) — только внутренний контур.

### DELETE /api/auth/telegram/link/{code} — internal

X-Internal-Token. Инвалидировать код («Отмена» у жертвы атаки). Ответ
`{"status": "cancelled"}`; 404 — код истёк/не найден.

### POST /api/auth/telegram/link/confirm — internal

X-Internal-Token. Бот подтверждает `/start login_<code>` — **только после**
явного «✅ Это я» пользователя. Body: `code (8..128)`, `telegram_id: int`,
опционально `username/first_name/last_name/language_code`,
`user_acknowledged_at?: int` (unix, для аудита). Ответ `{"status": "ok"}`;
404 — код истёк/повреждён.

---

## Профиль

Роутеры `me.py` (JWT) и `users.py` (internal).

### GET /api/me — JWT

Профиль текущего пользователя из токена. Ответ — `UserOut` (см. пример в
AuthResponse выше; `password_hash`/`email` в схему не входят). 404 — пользователя
уже нет.

### POST /api/me/onboarded — JWT

Пометить онбординг пройденным. **Идемпотентно**: первый вызов сохраняет
`onboarded_at`, повторные — no-op (факт первого прохождения для когорт).
Ответ — `UserOut`.

### GET /api/me/analyses — JWT

История «Мои анализы» (AI-разборы + карточка машины). Query: `limit`
(1..100, дефолт 50). Ответ — список новых сверху:

```json
[{"id": 1, "listing_id": 42, "verdict": "fair", "fair_min": 10500000.0,
  "fair_max": 11900000.0, "summary": "...", "created_at": "2026-10-01T10:00:00Z",
  "make": "Kia", "model": "Sorento", "year": 2021, "price": 11200000.0,
  "currency": "KRW", "image": "https://..."}]
```

### POST /api/users/upsert — internal

X-Internal-Token. Создать/обновить пользователя по данным Telegram (для бота).
Body: `telegram_id: int`, опционально `username/first_name/last_name/language_code`.
Идемпотентно по `telegram_id`; `is_active` не меняется (бан не снимается).
422 — `telegram_id <= 0` (гостей бот не создаёт). Ответ — `UserOut`.

---

## Лента и карточки

Роутер `listings.py`, префикс `/api/listings`. Лента/карточка/facets/by-url —
**без авторизации** (публичный каталог); тарифные гейты — на конкретных
подручках (см. ниже).

### GET /api/listings

Лента объявлений с фильтрами. Query-параметры (все опциональны):

| Параметр | Тип | Ограничение |
|---|---|---|
| `make`, `model` | str | подстрока/точное значение бренда/модели |
| `year_from`, `year_to` | int | `ge=1900` |
| `price_min`, `price_max` | float | `ge=0` |
| `currency` | str | код валюты (KRW/USD/RUB…) |
| `mileage_min`, `mileage_max` | int | `ge=0` |
| `body_type`, `fuel`, `transmission`, `drive`, `color`, `source` | str | значения из facets/справочников; `source=market` — лоты внутреннего рынка (только accepted) |
| `engine_volume_min`, `engine_volume_max` | float | `0..20` (литры) |
| `cursor` | str | keyset-продолжение, только с `sort=new` |
| `sort` | str | `new\|price_asc\|price_desc\|mileage_asc\|year_desc`, дефолт `new` |
| `limit` | int | 1..50, дефолт 20 |
| `offset` | int | `ge=0`, дефолт 0 |

Ответ `ListingListOut`: `{"total": int, "items": [ListingOut], "next_cursor": str|null, "has_more": bool}`.
Элемент `ListingOut` — карточка: `id, source, source_id, url, make, model, year,
price, currency, mileage_km, vin, body_type, fuel, transmission, drive,
engine_volume, color, images[], options[], parsed_at, is_active,
has_inspection, has_market, owner_type, owner_description, owner_history[],
first_seen_at, first_price, price_history[]|null, updated_at, last_seen_at,
price_changed_at, title_raw, title_ru, source_language`.

Ошибки: 422 — инверсии диапазонов (`year_from > year_to` и т.п.) или курсор
не с `sort=new`; 400 — битый курсор `Invalid cursor`.

```bash
curl "http://localhost:8000/api/listings?make=Kia&year_from=2019&sort=new&limit=20"
# вторая страница keyset:
curl "http://localhost:8000/api/listings?sort=new&cursor=<next_cursor>"
```

### GET /api/listings/facets

Справочник зависимых селектов. Query: `make?` — модели выбранной марки; без —
список марок. Только по активным объявлениям; кэш Redis 300с. Ответ:
`{"makes": ["Kia", ...]}` или `{"models": ["Sorento", ...]}` (ключ по запросу).

### GET /api/listings/by-url

Объявление по ссылке (бот: «скинь ссылку»). Query: `url` (8..512). Ответ —
`ListingOut`. 404 — совпадений нет; 409 `Ambiguous URL, use listing id` —
несколько; 422 — URL слишком короткий для поиска.

### GET /api/listings/{listing_id}

Карточка (PDP) с историей цен. Path: `listing_id` (`ge=1`). Ответ — `ListingOut`
с заполненным `price_history: [{price, currency, recorded_at}]`. 404 — нет.

```bash
curl http://localhost:8000/api/listings/42
```

### GET /api/listings/{listing_id}/inspection — JWT

Отчёт осмотра (карта повреждений + чек-лист) для PDP. **Тарифный гейт**: Free →
403 `inspection_locked: данные чеков осмотра доступны на тарифах Pro и Business`;
админ видит всегда. 404 — осмотра нет или объявление неактивно. Ответ
`InspectionReportOut`: `source ('manual'|'encar'), inspector?, inspected_at?,
grade? ('excellent'|'good'|'fair'|'poor'), summary?, marks[], items[], images[]`;
`mark = {zone, type, note?, pos?}` (зоны кузова и типы повреждений —
`BODY_ZONES`/`MARK_TYPES` схемы `listing.py`), `item = {key, status
('ok'|'warn'|'defect'|'na'), note?}`.

### PUT /api/listings/{listing_id}/inspection — JWT + admin

Создать/заменить отчёт осмотра (ручной осмотр). Не-admin → 403 `Admin only`;
админ может осматривать и неактивные объявления. Body — `InspectionReportIn`:
`source ('manual'|'encar', дефолт manual)`, `inspector? (≤120)`, `inspected_at?`,
`grade?`, `summary? (≤1000)`, `marks[] (≤60)`, `items[] (≤40)`, `images[] (≤40)`.
Ответ — `InspectionReportOut`.

### GET /api/listings/{listing_id}/history-report — JWT

Отчёт истории лота (VIN/лот → русский отчёт): факты ДТП/владельцы/пробег из
чека + LLM-абзац «стоит ли смотреть». Гость → 403 (как AI-анализ). Нет данных →
404 `no_history_data`. Готовый отчёт кэшируется в Redis 24ч. Ответ:

```json
{"listing_id": 42, "facts": {"accidents": 1, "owners": 2, "...": "..."},
 "llm_summary": "…", "generated_at": "2026-10-28T12:00:00+00:00"}
```

---

## Избранное

Роутер `favorites.py`. Все ручки — JWT (гость может, это демо-функция).

### GET /api/favorites

Избранные пользователя (новые сверху). Query: `limit` (1..100, дефолт 50),
`offset` (`ge=0`), `cursor?` (keyset; при наличии `offset` игнорируется).
Битый курсор → 400 `Invalid cursor`. Ответ — `list[ListingOut]` (в карточках
заполнен `first_price` — бейдж «цена снизилась»).

### GET /api/favorites/{listing_id}

Статус: `{"favorited": true}`. 404 — объявления нет.

### POST /api/listings/{listing_id}/favorite

Toggle «В избранное» (атомарность в репозитории). Ответ `{"favorited": bool}`
— новое состояние. 404 — объявления нет.

```bash
curl -X POST -H "Authorization: Bearer $ACCESS_TOKEN" \
  http://localhost:8000/api/listings/42/favorite
```

### DELETE /api/listings/{listing_id}/favorite — 204

Удалить из избранного. **Идемпотентно** (нет записи — тоже 204). Тело пустое.

---

## Фильтры-подписки

Роутер `filters.py`, префикс `/api/filters`. Все ручки — JWT.

Общие схемы: `FilterCreate = {name?, criteria?: SearchCriteria, notify_mode?
('instant'|'digest'|'off', дефолт digest), quiet_hours? ("HH:MM-HH:MM")}`;
`FilterUpdate` — те же поля частично; `FilterOut = {id, name, criteria: dict,
notify_mode, quiet_hours, created_at, updated_at}`.

`SearchCriteria` (`app/domain/search/criteria.py`, используется и в фильтрах,
и в `trigger_fetch`): `make/model/generation?`, `year_from/to? (1900..2100)`,
`price_min/max? (Decimal ≥ 0)`, `currency? (≤8)`, `mileage_max?`, `fuel/
transmission/drive/body_type/color? (≤50)`, `engine_volume_min/max? (0..20)`,
`country? ("KR"|"DE"|...)`, `sources? (≤20)`, `location?`, `not_damaged?`,
`no_accidents?`, `seller_type?`, `notes? (≤1000)`. Строковые поля без переводов
строк и ≤100 (анти prompt-injection); инварианты `min <= max` — иначе 422.

### GET /api/filters

Список фильтров пользователя по `created_at`. Query: `limit` (1..200, дефолт
50), `offset`. Ответ — `list[FilterOut]`.

### POST /api/filters — 201

Создание. Лимит по тарифу: free — 3, pro — 20, business — без лимита (403 при
превышении). `notify_mode=instant` — только тариф Business, иначе 403
`instant_business_only`. Ответ — `FilterOut`.

```bash
curl -X POST -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name": "Sorento до 12 млн", "criteria": {"make": "Kia", "model": "Sorento", "price_max": 12000000}, "notify_mode": "digest"}' \
  http://localhost:8000/api/filters
```

### PATCH /api/filters/{filter_id}

Частичное обновление. Проверяется **итоговое** `notify_mode`: потерявший
Business не сохранит `instant` (403). Чужой фильтр → 404 (существование не
раскрывается). Ответ — `FilterOut`.

### DELETE /api/filters/{filter_id} — 204

Удаление (чужой → 404). Тело пустое.

---

## AI-анализ цены

Роутеры `analysis.py` (JWT `/api/listings/...`) и его `bot_router`
(`/api/bot/listings/...`, internal). Квоты тарифа: free — 1 разбор в **неделю**
(на все машины), pro — 30/день, business — без лимита (в контексте LLM — чек
осмотра). Гость → 403 `guest_forbidden: AI-анализ цены доступен только после
входа или регистрации`.

### POST /api/listings/{listing_id}/analysis — JWT

Запустить разбор цены через LLM (канал `web`). Ответ `AnalysisOut`:

```json
{"verdict": "fair", "price": 11200000.0, "currency": "KRW",
 "fair_min": 10500000.0, "fair_max": 11900000.0,
 "summary": "Цена в рынке...", "factors": ["пробег выше среднего"],
 "comps": {"count": 8}, "history": [], "source": "ai",
 "created_at": "2026-10-28T12:00:00+00:00"}
```

Ошибки: 403 — гость/деактивирован; 404 — листинга нет; 503
`AI temporarily unavailable`; 502 `Analysis payload is invalid` (битый ответ
upstream); 429 при исчерпании окна тарифа.

```bash
curl -X POST -H "Authorization: Bearer $ACCESS_TOKEN" \
  http://localhost:8000/api/listings/42/analysis
```

### GET /api/listings/{listing_id}/analysis — публично

Кэш последнего разбора (общий на листинг) **без вызова LLM** и без списания
квоты. Ответ — `AnalysisOut | null` (null — разбора ещё нет).

### POST /api/bot/listings/{listing_id}/analysis — internal

X-Internal-Token. Тот же разбор по каналу `bot`: body
`{"user_id": <telegram_id>}`. Guest-правило и квоты применяются одинаково.

### POST /api/listings/{listing_id}/similar — JWT

Похожие машины (SQL-кандидаты + LLM-реранк; при сбое LLM — фолбэк по цене,
`source: "stats"`). Ответ `SimilarOut`:

```json
{"source": "ai", "items": [{"id": 43, "make": "Kia", "model": "Sorento",
  "year": 2020, "price": 10800000.0, "currency": "KRW", "mileage_km": 78000,
  "image": "https://...", "url": "https://...", "reason": "тот же мотор, дешевле",
  "score": 0.87}]}
```

Ошибки: 404 — листинга нет; 503 — LLM недоступна.

---

## Сделки

Роутер `deals.py` (JWT, префикс `/api/listings`).

### POST /api/listings/{listing_id}/agent — 201 — JWT

Отправить агента на сделку/осмотр — **только тариф Business** (иначе 403).
Body: `{"comment?": str ≤ 500}`. Создаёт лид `source=agent` и deal-тред
(агент подключится в чат после claim). **Идемпотентно** по той же машине —
тот же лид и тот же тред. Ответ: `{"lead_id": int, "status": "requested",
"thread_id": "<uuid>|null"}`. 404 — листинга нет.

### POST /api/listings/{listing_id}/contact — 201 — JWT

Заявка «Связаться с партнёром/менеджером» — любой тариф, но не гость. Body:
`{"comment?": str ≤ 500}`. Создаёт лид `source=web` → пул партнёров (CPL).
Повторная заявка по той же машине в течение суток → 409 `duplicate_contact`.
Ответ: `{"lead_id": int, "status": "requested"}`. 404 — листинга нет.

```bash
curl -X POST -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "Content-Type: application/json" -d '{"comment": "Интересует торг"}' \
  http://localhost:8000/api/listings/42/contact
```

---

## Калькулятор

Роутер `calc.py`, префикс `/api/calc`. Обе ручки **без авторизации** — чистая
stateless-логика (тот же код в монолите и catalog-сервисе).

### POST /api/calc/delivery

Расчёт «под ключ» Корея → РФ/РБ: авто + море + брокер + СБКТС/ЭПТС + пошлина +
сборы + утильсбор. Body (`DeliveryIn`): `price_usd: float (0..1_000_000)`,
`engine_cc: int (50..10_000, дефолт 2000)`, `year: int (1980..2100)`,
`power_hp?: int (10..2000)`, `region: "ru"|"by" (дефолт ru)`. Год в будущем —
warning в лог (возраст отрицательный), расчёт продолжается.

Ответ `DeliveryOut`: `price_usd, engine_cc, year, age_years, age_group, region,
currency ("₽"/BY-эквивалент), power_hp, util_category, util_note, components[]
(постатейная разбивка), legal_refs[], total_rub, total_usd, total_local,
rates_note`. 400 — сервис не может обработать параметры (год вне таблицы и т.п.).

```bash
curl -X POST http://localhost:8000/api/calc/delivery \
  -H "Content-Type: application/json" \
  -d '{"price_usd": 8500, "engine_cc": 2400, "year": 2019, "region": "ru"}'
```

### POST /api/calc/compare

Сравнение до 10 вариантов. Body: `{"items": [{title? (≤200), price_usd,
engine_cc, year, power_hp?, region}, ...]}` (1..10 items; каждый item
валидируется как `DeliveryIn` — битый item даёт 422, а не выпадает). Ответ
`{"items": [...]}` отсортирован по `total_usd` (title по умолчанию
`"<year> г. · <cc> см³"`). 400 — вариант не считается.

---

## AI-поиск (web)

Роутер `ai_search.py`, префикс `/api/ai`. Все ручки — JWT; `user_id` только из
токена (IDOR-защита). Квота AI-поиска: free 3/день, pro 200/день, business
безлимит (`ai_daily_limit` тарифа).

### POST /api/ai/message

Один ход AI-агента: текст → ответ + опции + критерии. Body:
`{"text": str (1..4000)}`. Ответ `DialogReplyResponse`:

```json
{"session_id": "abc", "text": "Какой бюджет?", "options": ["до 10 млн", "10-15 млн"],
 "status": "active", "finalize_ready": false, "turn_count": 3,
 "criteria": {"make": "Kia"}}
```

Ошибки: 429 — суточный лимит; 503 `AI temporarily unavailable`; 404/403 —
пользователь пропал/забанен между запросами; 422 — InvalidInput сценария.

```bash
curl -X POST -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "Content-Type: application/json" -d '{"text": "Ищу Kia Sorento до 12 млн"}' \
  http://localhost:8000/api/ai/message
```

### POST /api/ai/reset

Сбросить состояние диалога. Без body. Ответ `{"reset": true}`.

### POST /api/ai/finalize

Финализация диалога: сохранить фильтр + поставить задачи парсинга источников.
Body: `{"criteria?: dict}` (критерии валидирует сценарий как SearchCriteria).
Ответ `{"criteria": {...}, "sources": ["encar", ...]}`.

### GET /api/ai/dialog/{session_id}

Архивный диалог — **только свой** (чужой/несуществующий → 404, существование
не раскрывается). Ответ `DialogHistoryOut`: `{session_id, status, criteria,
turn_count, messages[], created_at?, updated_at?}`.

---

## Диалог бота (internal)

Роутер `dialog.py`, префикс `/api/dialog`, весь — **X-Internal-Token**
(его вызывает Telegram-бот). Семантика и квоты — те же, что у `/api/ai`,
но `user_id` передаётся в теле.

### POST /api/dialog/message

Body: `{"user_id": int > 0, "text": str (1..4000)}`. Ответ —
`DialogReplyResponse` (формат как у `POST /api/ai/message`). Ошибки: 404 —
пользователя нет; 403 — деактивирован; 422 — InvalidInput; 429 — квота;
503 — LLM недоступна.

### POST /api/dialog/restore

Поднять последний диалог из архива («посмотреть диалог спустя время»).
Body: `{"user_id": int > 0}`. Ответ `{"restored": bool, "reason?": str,
"session_id?": str}`; архива нет → `{"restored": false, "reason": "not_found"}`;
внутренний сбой → 500 `Restore failed`.

### POST /api/dialog/reset

Body: `{"user_id": int > 0}`. Ответ `{"reset": true}`.

### POST /api/dialog/finalize

Body: `{"user_id": int > 0, "criteria?: dict}`. Ответ
`{"criteria": {...}, "sources": [...]}` — как web-версия.

---

## Чаты (JWT)

Роутер `chats.py`, префикс `/api/chats`. Весь юзерский контур — JWT; **гость
отвергается целиком**: 403 `chats_registration_required: чаты доступны после
входа или регистрации`. Маппинг кодов сценариев: `not_found → 404`,
`forbidden → 403`, `conflict → 409`, `invalid_input → 400`.

Треды: типы `support` (singleton поддержки с RAG-ботом), `seller` (чат с
продавцом лота рынка), `deal` (переговоры по сделке с агентом), `group`
(переговорная до 5 человек). Создание тредов идемпотентно (partial unique):
создан → 201, reuse → 200.

### GET /api/chats

Мои треды (владение или участие) с unread и предметом. Ответ — `list[ThreadOut]`:

```json
[{"id": "0f1e...", "type": "support", "title": "Поддержка", "status": "open",
  "unread": 2,
  "last_message": {"body": "Добрый день!", "created_at": "...",
                   "sender": "user", "sender_id": 123},
  "subject": {"id": 42, "make": "Kia", "model": "Sorento", "year": 2021,
              "price": 11200000.0, "currency": "KRW", "url": "...", "image": "..."},
  "lead_id": null, "lead_status": null,
  "is_group": false, "participants": [],
  "role": "member", "muted": false}]
```

`participants[] = {user_type, user_id, name?, role ('owner'|'member')}` — для
переговорных; `lead_id/lead_status` — для deal-тредов (CTA «Отметить сделкой»
в кабинете партнёра).

### POST /api/chats/support — 201/200

Создать/reuse singleton поддержки. Ответ `{"thread_id": "<uuid>"}`; статус 201
при создании, 200 при reuse.

### POST /api/chats/seller/{listing_id} — 201/200

Чат с продавцом лота. Только лоты внутреннего рынка: внешние (encar/kcar без
owner) → 409 `market_lot_required: чат с продавцом доступен только для лотов
рынка`; лот снят с продажи → 409 `listing_not_active`. Ответ
`{"thread_id": "<uuid>"}`.

```bash
curl -X POST -H "Authorization: Bearer $ACCESS_TOKEN" \
  http://localhost:8000/api/chats/seller/42
```

### POST /api/chats/group — 201

Переговорная по автомобилю до 5 человек. Body: `{"title?": str ≤ 120 (пустой →
автогенер «Переговоры: Make Model Year»), "listing_id?": int ≥ 1}`. `listing_id`
обязателен фактически: без него → 409 `group_needs_listing`; лот неактивен →
409 `listing_not_active`. Создатель — owner; партнёр-владелец лота НЕ
добавляется автоматически (приглашение кодом). Ответ `{"thread_id": "<uuid>"}`.

### POST /api/chats/join — 201/200

Войти в переговорную по коду. Body: `{"code": str, ровно 6 символов}`.
201 — вошли; 200 — уже участник (тот же тред); 404 — код истёк/не найден;
409 `group_is_full` — участников уже 5. Ответ `{"thread_id": "<uuid>"}`.

### POST /api/chats/{thread_id}/invite

Код приглашения (owner only). Многоразовый до лимита участников, TTL 7 дней.
Не-group → 409; чужой тред → 404; member → 403 `invite_owner_only`.
Ответ `{"invite_code": "AB3D5F"}` (6 символов base32).

### DELETE /api/chats/{thread_id}/participants/{user_type}/{user_id}

Owner исключает участника любого типа (`user_type`: `user|partner|operator`).
Кик owner → 409 `cannot_kick_owner`; кик себя → 409 (подсказка использовать
leave); не-владелец → 403; цели нет → 404. Ответ `{"removed": true}`.

### POST /api/chats/{thread_id}/leave

Покинуть переговорную. Member — свободно; owner — только последний участник
(иначе 409 `owner_must_archive`); участников 0 — тред закрывается сам.
Ответ `{"left": true}`.

### POST /api/chats/{thread_id}/archive

Архивировать (owner): `closed` + system-строка. **Идемпотентно** (повторный
archive → 200 без изменений). Сообщения в архив → 403 `thread_archived`;
переоткрытия нет (MVP). Ответ `{"archived": true}`.

### POST /api/chats/{thread_id}/mute и /unmute

Выключить/включить **свои** уведомления треда (muted-тред не инкрементит бейдж).
Ответ `{"muted": true|false}`.

### POST /api/chats/{thread_id}/report — 201/200

Жалоба (участник). Body: `{"reason": "spam|fraud|offtop|abuse",
"comment?": str ≤ 1000}`. **Идемпотентна**, пока открыта: повтор с той же
причиной → 200 `{"reported": false}` без новой строки (partial unique);
новая жалоба → 201 `{"reported": true}`.

### GET /api/chats/ws-token

Браузерный JWT centrifugo (`sub=user:{telegram_id}`, exp +12ч) + адрес сервера.
Ответ `{"token": "<jwt>", "url": "<centrifugo public url>"}`. Гость → 403.

```bash
curl -H "Authorization: Bearer $ACCESS_TOKEN" \
  http://localhost:8000/api/chats/ws-token
```

### GET /api/chats/online

Онлайн-статусы юзеров (presence их личных каналов). Query: `ids` — до 50
telegram_id через запятую (строка 1..600 символов). Мусор/превышение → 400
`online_ids_invalid`. Ответ `{"online": [123, 456]}`. Centrifugo не настроен —
честный пустой список.

### POST /api/chats/subscribe-proxy

Валидация подписки: centrifugo → бэкенд. Auth: `X-Internal-Token` =
INTERNAL_TOKEN/APP_SECRET_KEY **или** CENTRIFUGO_HTTP_API_KEY (статический
заголовок в конфиге centrifugo). Body (формат centrifugo v6):
`{client?, channel, user?, data?}` — `user` это connection-JWT (sub).
Разрешено → `{}` (200); запрещено → `{"error": {"code": 403, "message":
"subscription denied"}}` — тоже 200 (не-2xx считается временной ошибкой и
ретраится centrifugo). Правила: `user:{X}` — только самому X; `thread:{uuid}` —
только участникам. Битый токен заголовка → 401.

### GET /api/chats/{thread_id}/messages

Сообщения по возрастанию id. Query: `before_id? (≥1)`, `after_id? (≥0)`
(взаимоисключающие), `limit` (1..100, дефолт 50). Не-участник → 404.
Ответ — `list[MessageOut]`: `{id, sender_type ('user'|'partner'|'operator'|
'system'|'bot'), sender_id?, body, created_at}`.

### POST /api/chats/{thread_id}/messages — 201

Отправить сообщение. Body: `{"body": str (1..4000, markdown)}`. Заголовок
`Idempotency-Key?` (≤128): повтор запроса с тем же ключом того же отправителя
возвращает сохранённое сообщение со статусом **200** без новой записи,
публикации и запуска RAG-бота (TTL ключа 24ч; Redis лежит — отправка не
блокируется, fail-open). **Антифрод**: 20 сообщений/мин на (юзер, тред) —
сверх → 429 `message_rate_limit`. В support-треде ответ RAG-бота готовится
фоном: REST не ждёт LLM (ответ по сокету или поллингом `after_id`). Тред в
архиве → 403 `thread_archived`; не-участник → 404.

```bash
curl -X POST -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "Content-Type: application/json" -H "Idempotency-Key: msg-123" \
  -d '{"body": "Здравствуйте! Торг возможен?"}' \
  http://localhost:8000/api/chats/0f1e.../messages
```

### POST /api/chats/{thread_id}/summon-operator

«Позвать человека» (только support-тред; иначе 409 `not_support_thread`).
**Идемпотентно**: повторный зов при стоящей паузе — тот же 200 без второй
system-строки. Чужой тред → 404. Ответ `{"bot_paused": true,
"message": {…MessageOut…}|null}`.

### POST /api/chats/{thread_id}/read

Поднять read-маркер. Body: `{"last_read_message_id?": int ≥ 0}` — без поля
«прочитать всё до последнего». Маркер монотонный (не опускается). Ответ
`{"last_read_message_id": <итог>}`.

### POST /api/chats/{thread_id}/typing

«Печатает» — эфемерное событие ДРУГИМ участникам (себе не доставляется).
Rate 1 событие / 2с на (юзер, тред): второе в окне → 429 `typing_rate_limit`.
Не-участник → 404. Ответ `{"typing": true}`.

### GET /api/chats/{thread_id}/link-preview

Превью ссылки. Query: `url` (1..2048). `/listing/N` → карточка из БД; внешняя →
OG-теги. SSRF-фильтр: только http/https, приватные IP запрещены → 400
`link_forbidden`; тело ≤512KB, не более одного редиректа; кэш 24ч.
Ответ — dict с полями превью (title/description/image…).

---

## Внутренний рынок (JWT)

Роутер `market.py` (`market_router`), префикс `/api/market`. Жизненный цикл
лота (10 §1): `draft → (moderation) → accepted | rejected`; правки accepted
→ `pending_patch`. Placement (§3): первый лот — free-период
`MARKET_FREE_DAYS` (7 дней, кулдаун `MARKET_FREE_COOLDOWN_DAYS` = 30 дней)
или включён в тариф; дополнительные — Stars. Продление: 1 нед =
`MARKET_WEEK_STARS` (20★), 4 нед = x3 (60★).

### GET /api/market/offers — JWT

Витрина: лоты рынка + partner note + бейдж. Query: `make? (≤64)`,
`price_max? (>0)`, `limit` (1..100, дефолт 20). Ответ — `list[MarketOfferOut]`:
`{listing_id, partner_id, note?, make?, model?, year?, price?, currency?,
mileage_km?, url?, image?, has_market: true}`.

### POST /api/market/photos — 201 — JWT

Загрузить фото лота. Multipart: `file` — ≤8 МБ, JPEG/PNG/WebP (тип по магическим
байтам, content-type из multipart не доверяется); публичный bucket MinIO.
Ответ `{"url": "https://.../photo.jpg"}`. Гость → 403; >8 МБ → 413
`file_too_large`; не-изображение → 415; хранилище недоступно → 503
`photos_unavailable`.

```bash
curl -X POST -H "Authorization: Bearer $ACCESS_TOKEN" \
  -F "file=@photo.jpg" http://localhost:8000/api/market/photos
```

### POST /api/market/description — JWT

Сгенерировать продающее описание лота (LLM, только из полей входа). Body:
`{make (1..64), model (1..64), year (1900..2100), price? >0, currency? ≤8,
mileage_km? ≥0, fuel? ≤32, transmission? ≤32, photos_count (0..10, дефолт 0)}`.
Ответ `{"text": "<400-800 знаков>"}`. Лимит free — 5 генераций/день → 429
`ai_generation_limit`; гость → 403; LLM недоступна → 503.

### POST /api/market/lots — 201 — JWT

Создать лот. Body `MarketLotIn`: `make/model (1..64), year (1900..2100),
price: Decimal > 0, currency: "KRW"|"USD"|"RUB", mileage_km? ≥ 0, fuel? ≤ 32,
transmission? ≤ 32, description? ≤ 2000, photos[] (≤10, только http/https —
иначе 422 `photo_url_invalid`), submit: bool = false`. `submit=false` → черновик
(не виден в ленте, placement не стартует); `submit=true` → синхронная
авто-модерация. Гость → 403.

Ответ `CreatedLotOut`: `{listing_id, placement_status, is_active,
needs_payment, status ("draft"|"accepted"|"moderation"), quality?:
{score: 0-100, tips[]}}`.

```bash
curl -X POST -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"make": "Kia", "model": "Sorento", "year": 2021, "price": 11500000,
       "currency": "KRW", "photos": ["https://.../1.jpg"], "submit": true}' \
  http://localhost:8000/api/market/lots
```

### POST /api/market/lots/{listing_id}/submit

Отправить лот на публикацию (из draft/rejected). Вердикт синхронный.
Ответ `SubmitLotOut`: `{listing_id, status ("accepted"|"moderation"|
"rejected"), placement_status, is_active, needs_payment, rejected_reason?,
violations[]}`. Повторный submit возможен после исправления rejected.
Непригодное состояние → 409 `invalid_state`.

```bash
curl -X POST -H "Authorization: Bearer $ACCESS_TOKEN" \
  http://localhost:8000/api/market/lots/42/submit
```

### PATCH /api/market/lots/{listing_id}

Версионные правки: draft/rejected — напрямую; accepted — патч уходит в
`pending_patch` (лента показывает старые данные до решения админа);
moderation/archived/expired → 409 `invalid_state`. Body — подмножество полей
`MarketLotIn` (все опциональны, только переданные меняются; `photos` — только
http/https). Ответ — `MyLotOut`.

### GET /api/market/my-lots

Мои объявления: карточка + placement-статусы и дедлайны. Ответ — `list[MyLotOut]`:
`{listing_id, placement_status, is_active, placement_free_until?,
placement_paid_until?, placement_deadline?, make?, model?, year?, price?,
currency?, mileage_km?, images[], url?, owner_description?, status,
rejected_reason?, violations[], pending_patch?, quality?}`.

### DELETE /api/market/lots/{listing_id}

Снять лот с продажи (`is_active=false`). Чужой/несуществующий → 404
`lot_not_found`. Ответ `{"removed": true}`.

### POST /api/market/lots/{listing_id}/extend

Инвойс Stars на продление размещения (kind `placement`). Body:
`{"weeks": 1|4}` (иное → 400 `invalid_weeks`). Создаёт pending-платёж +
`createInvoiceLink` (currency XTR); фактическое продление (`placement_paid_until`)
делает вебхук `POST /api/payments/success` — повторная доставка идемпотентна.
`BOT_TOKEN` не задан → 503; Telegram недоступен → 502. Ответ
`PlacementInvoiceOut`: `{payment_id, invoice_url, amount (Stars), currency:
"XTR", listing_id, weeks}`.

```bash
curl -X POST -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "Content-Type: application/json" -d '{"weeks": 4}' \
  http://localhost:8000/api/market/lots/42/extend
```

---

## Подписки и платежи

Роутер `payments.py`. Тарифы: Pro 500★/мес (год 4800★ = −20%), Business
2500★/мес (год по формуле `2500*12*0.8`); free бесплатен. Валюта Telegram
Stars (XTR), провайдер — Payments в Telegram.

### GET /api/subscription — JWT

Статус подписки и лимиты тарифа для Mini App. Ответ `SubscriptionOut`:

```json
{
  "tier": "free", "paid_tier": "free", "subscription_until": null,
  "max_filters": 3, "ai_daily_limit": 3, "instant": false,
  "prices": {"pro_month": 500, "pro_year": 4800, "business_month": 2500, "business_year": 24000},
  "digest": "weekly", "inspection": false, "agent": false,
  "analyses_limit": 1, "analyses_window_days": 7
}
```

(`tier` — эффективный тариф, `paid_tier` — купленный, может быть истёкшим;
`null`-лимиты = без ограничения.)

### POST /api/payments/stars — JWT

Инвойс Stars для Mini App (→ `Telegram.WebApp.openInvoice`). Body:
`{"tier": "pro"|"business", "period": "month"|"year" (дефолт month)}` —
`year` → `period_days=365`, иначе 30. Создаёт pending-платёж (payload
`sub:<payment_id>`) и вызывает `createInvoiceLink`. `BOT_TOKEN` не задан → 503;
сбой Telegram → 502 `Telegram invoice creation failed`. Ответ `StarsInvoiceOut`:
`{payment_id, invoice_url, amount, currency: "XTR", tier, period_days}`.

```bash
curl -X POST -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "Content-Type: application/json" -d '{"tier": "pro", "period": "month"}' \
  http://localhost:8000/api/payments/stars
```

### GET /api/users/{user_id}/subscription — internal

X-Internal-Token. Статус тарифа для бота (команда `/subscribe`). Path:
`user_id`. Ответ `{"user_id", "tier", "subscription_until"}`; 404 — пользователя
нет.

### POST /api/payments/prepare — internal

X-Internal-Token. Pending-платёж для бота (бот сам вызовет `sendInvoice`).
Body: `{user_id: int, tier: "pro"|"business", period_days: int (1..400)}`.
Ответ `PaymentPrepareOut`: `{payment_id, amount, currency: "XTR", tier,
period_days, title, description}`.

### POST /api/payments/success — internal (webhook бота)

X-Internal-Token. `successful_payment` от бота: платёж → `paid`, эффект по
kind. Body: `{payment_id, user_id, provider_payment_id, amount}`. kind
`subscription` — подписка активирована; kind `placement` — размещение лота
продлено (`placement_paid_until`). **Идемпотентно**: повторная доставка для
платежа в статусе paid → 409 (Conflict), подписка не продлевается дважды.
Ответ `{activated: bool, tier?, subscription_until?, kind, listing_id?,
placement_paid_until?}`. 404 — платёж не найден.

---

## Партнёрский API

Роутеры `partners.py` (`/api/partner`, X-API-Key) и `market.py`
(`partner_router` — зеркала чатов/рынка в chat-сервисе). Ключ: заголовок
`X-API-Key: pi_...`; окно `partner_rate_limit_per_min` (60/мин) — превышение
429. Гостевое ограничение не применяется (контур без JWT).

### GET /api/partner/me

Состояние счёта для ЛК. Ответ `PartnerMeOut`:

```json
{"id": 7, "name": "Авто-Партнёр", "plan": "starter",
 "daily_quota": 10, "claimed_today": 3, "quota_remaining": 7,
 "balance": 1500.0, "cpl_cost": 250.0, "key_prefix": null,
 "placement_slots": 10, "placement_slots_used": 2}
```

(`key_prefix` в этой ручке всегда null.) 404 — партнёр исчез.

### GET /api/partner/leads

Пул лидов (без PII) или мои лиды (с PII). Query: `mine` (bool, дефолт false),
`limit` (1..100, дефолт 20), `offset`. Пул — `PartnerLeadPublicOut`
`{id, source?, status, comment?, qualification?, created_at}`; `mine=true` —
`PartnerLeadOwnedOut` = public + `{user_id, partner_status?}`.

### POST /api/partner/leads/batch

Забрать до N лидов одной кнопкой (атомарно, `SKIP LOCKED`). Body:
`{"limit": 1..10, дефолт 3}`. Ответ `{"claimed": [ClaimOut...]}`, где
`ClaimOut = {lead_id, partner_id, partner_status, user_id, qualification?,
claimed_at?}`. Квота исчерпана → 429 `Daily quota exceeded`; баланс < CPL →
402 `Insufficient balance: top up to claim leads`.

```bash
curl -H "X-API-Key: pi_..." -X POST -H "Content-Type: application/json" \
  -d '{"limit": 5}' http://localhost:8000/api/partner/leads/batch
```

### POST /api/partner/leads/{lead_id}/claim

Эксклюзивно забрать одного лида (CPL-списание, атомарный `UPDATE … RETURNING`).
Ответ — `ClaimOut` (с PII). Ошибки: 409 `Lead claim failed: taken` — занят;
404 `Lead not found`; 429 — дневная квота; 402 — баланс; 500 — инвариант
нарушен (логируется).

### POST /api/partner/leads/{lead_id}/feedback

Статус качества своего лида. Body: `{"status": "accepted"|"returned"|"junk"|"deal"}`.
Чужой/не свой лид → 404 `Lead not found or not claimed by you`. Ответ —
`ClaimOut`.

### GET /api/partner/billing

План, баланс, расход квоты и каталог тарифов. Ответ `BillingOut`:
`{plan, daily_quota, claimed_today, quota_remaining, balance, cpl_cost,
plans: [{key, title, daily_quota, cpl_cost, description?}]}`.

### GET /api/partner/market

Сводка рынка + выгодные лоты (инструмент сотрудников партнёра). Query:
`make? (≤64)`, `model? (≤64)`, `limit` (1..50, дефолт 10). Без make — срез по
всему каталогу. Ответ `MarketOut`: `{make?, model?, count, min_price?,
max_price?, median_price?, deals: [{id, make?, model?, year?, price, currency?,
mileage_km?, url, discount_pct}]}` — «выгодный» = дешевле 93% медианы.

### GET /api/partner/offers

Витрина своего счёта (мои лоты + заметки). Ответ — `list[MarketOfferOut]`
(как у `/api/market/offers`, но только свои).

### POST /api/partner/offers — 201/200

Выставить лот в витрину (201) или обновить свою заметку (200). Body:
`{"listing_id": int ≥ 1, "note?": str ≤ 500}`. Чужой лот → 409.
Ответ `{"listing_id": <id>}`.

### DELETE /api/partner/offers/{listing_id}

Снять свой лот с витрины. Чужой/несуществующий → 404 `offer_not_found: лот не
в вашей витрине`. Ответ `{"removed": true}`.

### GET /api/partner/chats

Seller/deal-треды счёта с unread по участнику-партнёру. Ответ —
`list[ThreadOut]` (формат как у `GET /api/chats`).

### GET /api/partner/chats/ws-token

Браузерный JWT кабинета (`sub=partner:{id}`, exp +12ч). Ответ
`{"token", "url"}`.

### GET /api/partner/chats/online

Онлайн-статусы юзеров (presence `user:{tg_id}` — те же личные каналы).
Query: `ids` — как у юзерской ручки. Ответ `{"online": [...]}`.

### POST /api/partner/chats/join — 201/200

Партнёр входит в переговорную по коду (зеркало `/api/chats/join`; участник —
partner, те же лимиты до 5). Body `{"code": 6 символов}`; 404 — код истёк;
409 `group_is_full`. Ответ `{"thread_id": "<uuid>"}`.

### GET /api/partner/chats/{thread_id}/messages

Сообщения треда счёта (участие проверяет сценарий; чужой → 404). Query:
`before_id?/after_id?/limit (1..100, дефолт 50)` — как у юзерской ручки.

### POST /api/partner/chats/{thread_id}/messages — 201

Сообщение партнёра в тред. Body `{"body": str (1..4000)}`. Заголовок
`Idempotency-Key?` — семантика та же (повтор → 200 с сохранённым сообщением).
Антифрод: 20 сообщений/мин на (партнёр, тред) → 429 `message_rate_limit`.
Ответ — `MessageOut`.

```bash
curl -X POST -H "X-API-Key: pi_..." -H "Content-Type: application/json" \
  -H "Idempotency-Key: pmsg-1" -d '{"body": "Да, торг возможен"}' \
  http://localhost:8000/api/partner/chats/0f1e.../messages
```

### POST /api/partner/chats/{thread_id}/read

Read-маркер партнёра. Body `{"last_read_message_id?": int ≥ 0}`. Ответ
`{"last_read_message_id": <итог>}`.

### POST /api/partner/chats/{thread_id}/typing

«Печатает» от партнёра (зеркало юзерской; rate 1/2с → 429 `typing_rate_limit`).
Ответ `{"typing": true}`.

### POST /api/partner/chats/{thread_id}/summary

LLM-выжимка треда (что хочет клиент, обсуждаемые лоты, сомнения, следующий
шаг; ≤600 знаков, последние ≤50 сообщений). Кэш 1 час по последнему сообщению.
LLM не настроена → 503; чужой тред → 404. Ответ `{thread_id, summary,
last_message_id, generated_at}`.

### POST /api/partner/chats/{thread_id}/report — 201/200

Жалоба партнёра (зеркало юзерской; идемпотентна, пока открыта). Body:
`{"reason": "spam|fraud|offtop|abuse", "comment? ≤ 1000"}`. Ответ
`{"reported": bool}`.

### GET /api/partner/market/packages

Пакеты размещений из конфига (дефолт `{"10": 500, "50": 1800, "200": 5000}` ₽).
Ответ — `list[{size, price_rub}]` (без `slots_free`).

### POST /api/partner/market/packages — 201

Купить пакет со баланса: `balance -= price`, `slots += size` (атомарно).
Body: `{"size": int > 0}` — размер из каталога. Не хватает баланса → 402
`insufficient_balance`. Ответ `{size, price_rub, slots_free}` (остаток мест).

```bash
curl -X POST -H "X-API-Key: pi_..." -H "Content-Type: application/json" \
  -d '{"size": 10}' http://localhost:8000/api/partner/market/packages
```

### GET /api/partner/market/lots

Размещения счёта (включая снятые) со placement-статусом slot. Ответ —
`list[MyLotOut]`.

### POST /api/partner/market/lots — 201

Разместить лот за слот (бессрочный, без placement-дат). Body — `MarketLotIn`
(`submit` игнорируется: публикация автоматическая; ~10% лотов уходит в ручную
модерацию — сэмплинг контроля качества, статус в ответе). Нет свободных
слотов → 409 `placement_slots_exhausted`. Ответ — `CreatedLotOut`.

### DELETE /api/partner/market/lots/{listing_id}

Снять размещение (слот **не возвращается**). Чужой → 404 `lot_not_found`.
Ответ `{"removed": true}`.

### GET /api/partner/listings/{listing_id}/inspection

Чек осмотра своего лота (без тарифного гейта). Чужой/несуществующий лот и
отсутствие чека неотличимы — 404. Ответ — `InspectionReportOut`.

### PUT /api/partner/listings/{listing_id}/inspection

Создать/заменить чек своего лота. Тело — `InspectionReportIn` (как у admin);
`source` форсируется в `'partner'` независимо от тела (аудит происхождения).
Чужой лот → 404. Ответ — `InspectionReportOut`.

### PUT /api/partner/listings/{listing_id}/history

Заменить историю машины своего лота (публично читается в PDP
`ListingOut.owner_history`). Body: `{"events": [{date: date, title: 1..120,
note? ≤ 300}, ...]}` — до 50 событий, сохраняется сортировка по дате убыванием.
Ответ `{"updated": true}`.

---

## Лиды (internal)

Роутер `leads.py`, префикс `/api/leads`, весь — X-Internal-Token (бот/Mini App
через бэкенд-прокси, менеджеры).

### POST /api/leads — 201

Создать лид. Body: `{user_id: int, listing_id?: int, source?: str
("bot"|"miniapp"|"web"), comment?: str ≤ 2000}`. Заголовок `Idempotency-Key?`:
повторный запрос с тем же ключом возвращает существующий лид (201 с тем же
телом), а не дубль. Ошибки: 404 — user/listing не найдены; 409 — конфликт
вставки. Ответ `LeadOut`: `{id, user_id, listing_id?, source?, status,
manager_id?, comment?, created_at}`.

```bash
curl -X POST -H "X-Internal-Token: $INTERNAL_TOKEN" \
  -H "Content-Type: application/json" -H "Idempotency-Key: lead-42" \
  -d '{"user_id": 123456789, "listing_id": 42, "source": "bot",
       "comment": "Хочет торг"}' \
  http://localhost:8000/api/leads
```

### PATCH /api/leads/{lead_id}

Частичная смена статуса/комментария/менеджера. Body: минимум одно из
`{status?: "new"|"in_progress"|"closed"|"rejected", manager_id?: int,
comment?: str ≤ 2000}` — пустой патч → 422 (схема). Непереданные поля не
меняются. 404 — лида нет; 422 — manager_id не существует. Ответ — `LeadOut`.

---

## Уведомления (internal)

Роутер `notifications.py`, префикс `/api/notifications`, весь — X-Internal-Token
(бот: команды `/stop`, `/resume`).

### POST /api/notifications/settings

Применить режим уведомлений и/или тихие часы **ко всем фильтрам пользователя**.
Body: `{user_id: int > 0, notify_mode?: "instant"|"digest"|"off" (None = не
менять), quiet_hours?: "HH:MM-HH:MM" (валидация схемой: формат HH:MM-HH:MM,
start != end — иначе 422), quiet_hours_clear?: bool (явный сброс)}`.
Ошибки: 404 — пользователь не найден; 403 — деактивирован или `instant` без
Business; 422 — конфликт `quiet_hours` и `quiet_hours_clear`. Ответ
`{user_id, updated: <кол-во фильтров>, detail?}`.

### POST /api/notifications/scan

Ручной запуск скана фильтров (обычно — Fetch Worker). **Идемпотентность**: не
более 1 скана в минуту — повторный POST в окне → 409. Очередь недоступна → 503.
Ответ `{"enqueued": true, "job_id": "<str>|null"}`.

---

## Admin

Все ручки — **X-Internal-Token** (route-класс `InternalDishkaRoute`; админ-панель
входит по внутреннему токену, персонального operator-id нет — MVP). Источники:
роутеры `admin.py`, `partners.admin_router`, `chats.admin_router`,
`market.admin_router`, `metrics.py`.

### GET /api/admin/adapters

Классы адаптеров парсинга (валидация при создании источника). Ответ
`{"adapters": ["encar", ...]}`.

### GET /api/admin/leads

Лиды для админ-панели (read-only). Query: `status?` (alias параметра
`lead_status`), `partner_id? (≥1)`, `limit (1..200, дефолт 50)`, `offset`.
Ответ — `list[dict]` (строки лидов с контекстом).

### GET /api/admin/sources

Все источники (включая выключенные). Ответ — `list[SourceConfig]`:
`{name, adapter_class, kind ("api"|"parser"|"hybrid"), enabled, priority,
schedule_cron?, rate_limit_per_min, max_concurrency, timeout_sec, retry_policy,
credentials_ref?, capabilities, config}`.

### PUT /api/admin/sources/{name}

Создать/обновить источник. `adapter_class` обязан быть из `/api/admin/adapters`
(иначе 422); `base_url` проходит SSRF-валидацию (http(s), не private/loopback);
`credentials_ref` — только с разрешённого префикса. Ошибки: 422 — невалидный
конфиг; 409 — конфликт. Ответ — сохранённый `SourceConfig`.

```bash
curl -X PUT -H "X-Internal-Token: $INTERNAL_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name": "encar", "adapter_class": "encar", "kind": "api",
       "enabled": true, "priority": 100}' \
  http://localhost:8000/api/admin/sources/encar
```

### GET /api/admin/sources/{name}/health

Здоровье источника: `avg_latency_ms`, `error_streak`, `last_success_at`,
`last_error_at`, `total_fetched` и т.д. 404 — источника нет.

### POST /api/admin/sources/{name}/fetch

Поставить задачу парсинга в очередь `fetch`. Query: `criteria?` (dict,
валидируется как SearchCriteria — иначе 422), `max_pages (1..50, дефолт 1)`.
Ответ `{"queued": true, "job_id": "<message_id>"}`. 404 — источника нет;
503 — очередь недоступна.

### POST /api/admin/assistant/chat

AI-помощник админа (LLM с tools: leads/partners/sources). Body:
`{message: str (1..4000), history?: [{role: "user"|"assistant", content:
1..4000}] (≤20 ходов)}`. LLM недоступна → 503 (детали сбоя — только в лог).
Ответ `{"reply": "<текст>"}`.

### GET /api/admin/listings/{listing_id}/raw

Presigned URL на сырой payload объявления в MinIO (прямого доступа к хранилищу
админка не получает; короткий GET-линк, TTL `RAW_PRESIGN_TTL_SEC`). Ответ
`{"url": "..."}`. 404 — raw_key NULL/объявления нет; 502 — хранилище недоступно.

### GET /api/admin/analytics/funnel

Воронка search→dialog→lead→subscription. Query: `days (1..365, дефолт 30)`.
Ответ `{"days": 30, "stages": [...]}`.

### GET /api/admin/analytics/retention

Когорты D1/D7 по дню регистрации. Query: `days (1..365, дефолт 30)`. Ответ
`{"days": 30, "cohorts": [...]}`.

### Партнёры: /api/admin/partners

- `POST /api/admin/partners` — 201. Создать партнёра + первый API-ключ.
  Body (`PartnerCreateIn`): `name (1..128)`, `contact? (≤128)`,
  `plan ("starter"|"pro"|"salon", дефолт starter)`, `daily_quota (≥0, дефолт 10)`,
  `balance: Decimal (≥0)`, `cpl_rate?: Decimal > 0`, `owner_telegram_id?`
  (владелец получает бессрочный Business; пользователя нет → 422). Ответ
  `PartnerKeyOut`: `{partner_id, key: "<raw pi_... — показывается ОДИН раз>",
  key_prefix}`.
- `GET /api/admin/partners` — список партнёров + `claimed_today` и активный
  `key_prefix` (`PartnerWithStatsOut`).
- `POST /api/admin/partners/{id}/rotate` — ротация ключа (для suspended → 409
  «сначала активировать»). Ответ — `PartnerKeyOut` (новый raw-ключ один раз).
- `POST /api/admin/partners/{id}/revoke` — отозвать все ключи (security-аудит).
  Ответ — `PartnerOut`.
- `PATCH /api/admin/partners/{id}` — обновить `plan/daily_quota/status
  ("active"|"suspended")/cpl_rate/reset_cpl_rate/owner_telegram_id` (пишутся
  только изменения). Смена владельца/статуса синхронизирует тариф владельца:
  active → Business, suspended → free. Ответ — `PartnerOut`.
- `POST /api/admin/partners/{id}/billing` — пополнение (`amount > 0`, снимает
  suspended при исчерпании) или корректировка (`amount < 0`); `amount != 0`,
  `comment? ≤ 500`; атомарный `UPDATE … RETURNING`. Ответ — `PartnerOut`
  (новый баланс).

```bash
curl -X POST -H "X-Internal-Token: $INTERNAL_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name": "Авто-Партнёр", "plan": "starter", "daily_quota": 10,
       "balance": 5000}' \
  http://localhost:8000/api/admin/partners
```

### Модерация рынка: /api/admin/moderation

- `GET /api/admin/moderation` — очередь. Query: `queue ("moderation"|"patch",
  дефолт moderation)`, `limit (1..200, дефолт 50)`. `moderation` — лоты на
  проверке (риск по убыванию), `patch` — правки опубликованных. Ответ —
  `list[ModerationQueueItemOut]`: `{listing_id, owner_type, owner_user_id?,
  owner_partner_id?, status, risk_score, violations[], pending_patch?, make?,
  model?, year?, price?, currency?, mileage_km?, images[], owner_description?,
  created_at?}`.
- `POST /api/admin/moderation/{listing_id}/approve` — одобрить: accepted +
  placement-правила (партнёрскому лоту placement не нужен — слот оплачен).
  Решение уходит владельцу событием `listing_status` в личный канал.
- `POST /api/admin/moderation/{listing_id}/reject` — отклонить: rejected +
  причина. Body: `{"reason?": str ≤ 500}`.
- `POST /api/admin/moderation/{listing_id}/patch_accept` — применить
  версию-на-модерации к опубликованному лоту.
- `POST /api/admin/moderation/{listing_id}/patch_reject` — отклонить правки
  (`patch_reject_reason` внутри `pending_patch`). Body: `{"reason?": ≤ 500}`.

  Все четыре возвращают `ModerationOutcomeOut`: `{listing_id, status,
  placement_status?, reason?}`.

```bash
curl -X POST -H "X-Internal-Token: $INTERNAL_TOKEN" \
  http://localhost:8000/api/admin/moderation/42/approve
```

### Операторский контур поддержки: /api/admin/chats

- `GET /api/admin/chats` — треды для оператора. Query: `type
  ("support"|"seller"|"deal", дефолт support)`, `paused?` (true — только треды
  с бот-паузой), `limit (1..200, дефолт 50)`. Ответ — `list[OperatorThreadOut]`:
  `{id, type, status, bot_paused, user_id, user_name?,
  last_user_message_at?, last_user_message_snippet?}`.
- `GET /api/admin/chats/unread` — бейдж «ждут ответа» (юзер-сообщение позже
  последнего ответа оператора). Query: `type`. Ответ `{"threads": <int>}`.
- `GET /api/admin/chats/reports` — жалобы по статусу. Query: `status
  ("new"|"resolved"|"dismissed", дефолт new)`, `limit (1..200, дефолт 100)`.
  Ответ — `list[AdminReportOut]` (репортёр, причина, комментарий, сниппет
  последнего сообщения треда, `handled_at?`).
- `PATCH /api/admin/chats/reports/{report_id}` — обработать жалобу. Body:
  `{"status": "resolved"|"dismissed"}`. После обработки репортёр может
  пожаловаться повторно (partial unique держит только открытые). Ответ
  `{"status": "<новый>"}`.
- `GET /api/admin/chats/{thread_id}/events` — лента аудита треда
  (created/joined/invited/kicked/…), новые сверху. Query: `limit (1..500,
  дефолт 100)`. Ответ — `list[ChatEventOut]`: `{id, thread_id, actor_type,
  actor_id?, event, payload?, created_at}`.
- `DELETE /api/admin/chats/{thread_id}/participants/{user_type}/{user_id}` —
  оператор исключает любого участника, **включая owner** (событие `kicked` с
  actor=operator). Треда/участника нет → 404. Ответ `{"removed": true}`.
- `GET /api/admin/chats/{thread_id}/messages` — все сообщения треда
  (`before_id?/after_id?/limit 1..100 дефолт 50`). Ответ — `list[MessageOut]`.
- `POST /api/admin/chats/{thread_id}/messages` — 201. Ответ оператора:
  `sender_type='operator'`, репозиторий ставит `bot_paused=True` + публикация
  в канал треда и личный канал юзера. Body `{"body": str (1..4000)}`.
- `POST /api/admin/chats/{thread_id}/summary` — LLM-выжимка треда для
  оператора (последние ≤50 сообщений → ≤600 знаков; кэш 1 час). LLM не
  настроена → 503; треда нет → 404. Ответ `{thread_id, summary,
  last_message_id, generated_at}`.

---

## Вспомогательные сервисы

### RAG-сервис (`services/rag/rag_api`, порт 8100)

Профили `ai`/`micro`. Используется бэкендом для RAG-ответов бота поддержки
(не публично). Auth: заголовок `X-API-Token` или `Authorization: Bearer`
со значением `RAG_API_TOKEN` — иначе 401 `Invalid API token`.

- `POST /api/v1/ask` — вопрос по базе знаний. Body: `{question: str (1..4000),
  context?: dict}`. RPC через RabbitMQ (таймаут `ASK_TIMEOUT_SEC`, дефолт
  ответа 150с в compose): таймаут → 504 `RAG worker timeout`; ошибка воркера →
  502 `worker error: ...`. Ответ:

  ```json
  {"answer": "...", "sources": [{"doc_id": "...", "chunk_id": "...",
    "score": 0.83, "title": "...", "snippet": "..."}],
   "confidence": 0.9, "latency_ms": 1200, "cached": false,
   "conversation_id": null}
  ```

- `GET /health` — `{"status": "ok"|"degraded", "rabbit": bool}` (без токена).

### Translate-сервис (`services/translate`, порт 8200)

Перевод тайтлов NLLB (включается `TRANSLATE_ENABLED` + `TRANSLATE_SERVICE_URL`;
fallback — оригинальные тайтлы). Auth: `Authorization: Bearer <TRANSLATE_API_TOKEN>`
если токен задан, иначе открыт в доверенной сети.

- `POST /translate` — Body: `{texts: [str] (1..64), source_lang: "auto"|
  NLLB-код (дефолт auto), target_lang: "rus_Cyrl" (дефолт)}`. Ответ
  `{"translations": ["перевод", null, ...]}` — null = не удалось перевести.
  401 — неверный токен.
- `GET /health` — `{"status": "ok", "model": "<имя>", "loaded": bool}`.

---

## Справочник machine-кодов

Машино-читаемые коды в начале `detail` (список — по коду роутеров/сценариев;
тексты могут уточняться, коды стабильны):

| Код | HTTP | Где |
|---|---|---|
| `chats_registration_required` | 403 | весь `/api/chats` для гостя |
| `guest_forbidden` | 403 | AI-анализ для гостя |
| `inspection_locked` | 403 | чек осмотра на Free |
| `instant_business_only` | 403 | `notify_mode=instant` не на Business |
| `market_lot_required` | 409 | seller-чат для внешнего лота |
| `listing_not_active` | 409 | group/seller-чат по снятому лоту |
| `group_needs_listing` | 409 | переговорная без лота |
| `group_is_full` | 409 | в переговорной уже 5 участников |
| `invite_owner_only` | 403 | инвайт не владельцем |
| `cannot_kick_owner` | 409 | кик владельца комнаты |
| `owner_must_archive` | 409 | owner не может leave (не последний) |
| `thread_archived` | 403 | сообщение в архив |
| `not_support_thread` | 409 | зов оператора не в поддержке |
| `message_rate_limit` | 429 | >20 сообщений/мин на (субъект, тред) |
| `typing_rate_limit` | 429 | >1 typing / 2с |
| `already_reported` (повтор → 200 `reported=false`) | — | идемпотентность жалобы |
| `duplicate_contact` | 409 | повторная заявка contact за 24ч |
| `invalid_weeks` | 400 | extend не на 1/4 недели |
| `invalid_state` | 409 | PATCH/submit лота в непригодном статусе |
| `lot_not_found` / `offer_not_found` | 404 | чужой/несуществующий лот/витрина |
| `placement_slots_exhausted` | 409 | у партнёра нет слотов |
| `insufficient_balance` | 402 | баланс партнёра (claim, пакеты) |
| `file_too_large` | 413 | фото > 8 МБ |
| `unsupported_media_type` | 415 | не JPEG/PNG/WebP |
| `photos_unavailable` | 503 | MinIO выключен/лежит |
| `photo_url_invalid` | 422 | фото не http/https |
| `ai_generation_limit` | 429 | лимит генераций описания (5/день free) |
| `no_history_data` | 404 | нет данных для отчёта истории |
| `link_forbidden` | 400 | SSRF-фильтр link-preview |
| `online_ids_invalid` | 400 | мусор в `ids=` online-ручки |
| `Invalid cursor` | 400 | битый keyset-курсор |
| `Daily quota exceeded` | 429 | дневная квота claim |
| `Rate limit exceeded...` | 429 | окно партнёрских запросов |
| `Too many guest sessions...` | 429 | гостевые сессии с одного IP |
