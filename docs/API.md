# HTTP API

Приложение: `app/main_api.py` (mono) или доменные сервисы `services/*`
(micro; gateway `:8000` маршрутизирует по префиксам). OpenAPI —
`/docs` на каждом сервисе.

## Аутентификация

- **JWT** (Authorization: Bearer): `POST /api/auth/telegram`
  (initData Mini App), refresh-флоу, веб-логин, гостевые сессии
  (`POST /api/auth/guest`). Гостям закрыты: чаты целиком (403
  `chats_registration_required`), AI-анализ, размещения.
- **X-API-Key** — партнёрский кабинет (`/api/partner/*`).
- **X-Internal-Token** — админ-контур (`/api/admin/*`) и бот.

## Пользователь (JWT)

| Метод и путь | Назначение |
|---|---|
| GET `/api/me`, POST `/api/me/onboarded`, GET `/api/me/analyses` | профиль, онбординг, история разборов |
| GET `/api/listings` | лента (фильтры+sort=new keyset-пагинация; `source=market` — лоты рынка, только accepted) |
| GET `/api/listings/{id}` / `/{id}/inspection` / `/{id}/analysis` | PDP / чек осмотра (Pro+; free 403 inspection_locked) / кэш-разбор |
| POST `/api/listings/{id}/analysis` | AI-разбор цены (free 1/нед, pro 30/день, business безлимит+чек в контексте) |
| GET `/api/listings/{id}/history-report` | отчёт истории (ДТП-факты+LLM-вывод, кэш 24ч) |
| POST `/api/listings/{id}/similar` | похожие машины (SQL-кандидаты + LLM-реранк) |
| POST `/api/listings/{id}/contact` / `/{id}/agent` | лид «связаться» (всем) / агент на сделку (Business; создаёт deal-тред) |
| POST `/api/market/lots` / `/{id}/submit` / PATCH `/{id}` | создание лота (draft|submit) / отправка на модерацию / правки (accepted → pending_patch) |
| GET `/api/market/my-lots`, DELETE `/api/market/lots/{id}`, POST `/{id}/extend` | мои объявления (+quality/статусы), снять, продлить (Stars 20★/нед) |
| POST `/api/market/photos` | загрузка фото (multipart ≤8MB jpeg/png/webp → S3 URL) |
| POST `/api/market/description` | ✨ генерация описания (free 5/день) |
| GET `/api/chats`, POST `/api/chats/support`, POST `/api/chats/seller/{listing_id}` | список тредов / поддержка / чат с владельцем лота (внешние лоты → 409 market_lot_required) |
| POST `/api/chats/group` (обязателен listing_id), `/{id}/invite`, POST `/api/chats/join` | переговорная до 5 участников + инвайт-коды |
| GET/POST `/api/chats/{id}/messages`, POST `/{id}/read`, `/{id}/typing` | сообщения (Idempotency-Key, rate 20/мин), read-маркеры, typing |
| POST `/api/chats/{id}/summon-operator` / `/mute` / `/unmute` / `/leave` / `/archive`, DELETE `/{id}/participants/{type}/{id}`, POST `/{id}/report` | говернанс: оператор, mute, выход, архив (owner), кик (owner), жалобы |
| GET `/api/chats/ws-token`, GET `/api/chats/online?ids=` | Centrifugo JWT, онлайн-статусы (presence) |
| POST `/api/filters`…, GET `/api/subscription`, POST `/api/payments/stars` | сигналы-подписки (instant — Business), тариф, инвойсы Stars |
| POST `/api/calc` | калькулятор «под ключ» (РФ/РБ) |

## Партнёр (X-API-Key)

`/api/partner`: `me` (баланс/квота/слоты), чаты (`/chats`, сообщения,
read, typing, join, `/summary` — LLM-выжимка), лиды (пул/batch-claim/
фидбек), рынок (`/market` — медианы/выгодные), объявления
(`/market/packages` 10/50/200 мест со баланса, `/market/lots` CRUD),
чеки и история своих лотов (`/listings/{id}/inspection|history`),
витрина (`/offers`), биллинг.

## Админ (X-Internal-Token)

`/api/admin`: источники (CRUD/health/fetch), лиды, модерация
(`/moderation?queue=moderation|patch` + approve/reject/patch_accept/
patch_reject), поддержка (`/chats` операторские + сообщения + события
+ жалобы + kick + `/summary`), аналитика (funnel/retention), метрики.
