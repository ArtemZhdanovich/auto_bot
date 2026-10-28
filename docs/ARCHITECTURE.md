# Архитектура

## Слои (Clean Architecture)

- **`app/domain/`** — чистые правила: тарифы (`subscription/policies`),
  квоты анализов (`pricing/rules`), говернанс чатов, инжест
  (content_hash/dedup), маскировка контактов (`shared/contacts`).
- **`app/application/`** — сценарии (use cases) над портами: анализ
  цены, чаты (SendMessage/JoinByCode/SummonOperator/…), рынок
  (CreateMarketLot/SubmitLot/модерация/quality), история
  (BuildHistoryReport), описания (GenerateDescription), партнёры,
  биллинг. Порт → реализация через Dishka (`app/di`).
- **`app/infrastructure/`** — SQLAlchemy async (репозитории),
  RabbitMQ (legacy_worker: очереди fetch/send, cron-джобы), Redis,
  Centrifugo (publisher/presence), MinIO/S3 (photos/raw), LLM-клиент,
  адаптеры источников (encar/kcar + прокси-пул + NLLB-перевод).
- **`app/transport/`** — FastAPI-роутеры (thin), Telegram-бот (aiogram),
  HTTP/JWT-аутентификация (access/refresh, гости).
- **`services/`** — доменные сервисы профиля micro (gateway-маршруты
  по префиксам: /api/chats,/api/market → chat; /api/partner/* в chat
  и partners по правилам; /api/admin/* → admin и т.д.). Один и тот же
  код `app/` крутится в mono (`app/main_api`) и micro.

## Realtime: Centrifugo (один личный канал)

- Клиент подписывается **только** на личный канал `user:{tg_id}`
  (Mini App) / `partner:{id}` (кабинет); ws-token выдаёт
  `GET /api/chats/ws-token` (HS256, 12ч).
- Все события (message/read/typing/listing_status/lot_expired) бэк
  публикует в личные каналы участников
  (`ParticipantEventPublisher.publish_to_participants`); publish
  fire-and-forget (упал Centrifugo — лог warning, HTTP не падает).
- Subscribe-proxy `/api/chats/subscribe-proxy` (X-Internal-Token от
  Centrifugo) разрешает личный канал только своему sub, thread:{id} —
  только участникам. Namespaces `user|partner|thread` с
  `subscribe_proxy_enabled` + `presence` (онлайн-статусы через
  server-side presence API).
- Фронт: единый стор `lib/chatStore.ts` (треды/unread/typing TTL/
  авто-read) + транспорт с поллинг-фоллбэком (5с: список + after_id
  открытого треда).

## Данные и хранилища

- **PostgreSQL** — источник истины (30 миграций): users, listings
  (внешние + owner-лоты рынка со статусами lifecycle: draft/moderation/
  accepted/rejected/archived/expired + placement-даты + pending_patch),
  чаты (threads/messages/participants с ролями/mute, events, reports),
  партнёры (accounts/keys/tx, placement_slots), платежи, лиды.
- **MinIO** — `market-photos` (public-read: photos/ загрузки юзеров,
  `mirror/{source}/{source_id}/{NNN}.jpg` — зеркала фото внешних
  лотов с гарантией порядка) и `raw-payloads` (сырые ответы).
- **Redis** — кэши (facets, translate, history-report, chat-summary),
  rate-limits (сообщения 20/мин, typing 1/2с, описания 5/день),
  дайджест-буферы, invite-коды чатов, антидубли уведомлений.
- **Qdrant** — RAG-база знаний (отдельный сервис services/rag).

## Degradation

Ядро (HTTP) не требует периферии: Centrifugo → поллинг; MinIO →
503 photos_unavailable / raw NULL; translate → оригинал; RAG → LLM;
RabbitMQ → depends_on required:false (воркеры стартуют отдельно).
Ступени compose — docs/OPERATIONS.md.

## Frontend

- Mini App (React 18 + telegram-ui): keep-alive таб-экранов и
  листинга, единый чат-стор, темы (light/dark/system), лендинг-вход
  `/` для браузера (мини-апп/кабинет партнёра).
- Партнёрский кабинет `/partner` (API-key, свой css), админка `/admin`
  (internal-token): поддержка, модерация, лиды, источники.
