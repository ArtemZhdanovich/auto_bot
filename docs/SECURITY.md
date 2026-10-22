# Безопасность

Модель безопасности проекта. Журнал аудитов и инцидентов —
[INCIDENTS.md](../INCIDENTS.md) (INC-001…).

## Аутентификация и доступ

| Периметр | Механизм |
|---|---|
| Mini App | `initData` валидируется HMAC по bot_token с `max_age` (24 ч) → JWT HS256 (`APP_SECRET_KEY`) |
| Веб (вне Telegram) | логин/пароль: PBKDF2-SHA256, 240k итераций; access 1 ч / refresh 30 дней; magic-link — заглушка 501 |
| Internal API (`/api/admin/*`, `/metrics`, `/api/dialog/*`, `/api/notifications/*`, `/api/leads/*`) | отдельный секрет `X-Internal-Token`, сравнение `hmac.compare_digest`; проверка до dishka-резолва (`InternalDishkaRoute`) |
| Партнёрский API | `X-API-Key`: в БД только SHA-256, сырой ключ показывается один раз, ротация/ревокация мгновенные; rate limit 60 req/min |
| Gateway | только маршрутизация; токены проверяют доменные сервисы |

## Секреты

- `.env` не коммитится (в репо только `.env.example`); проверено по истории git.
- `APP_ENV=prod` → fail-fast на старте при дев-дефолте или ключе < 32 символов
  (`tests/security/test_prod_secrets.py`).
- JWT и internal API — разные секреты (`INTERNAL_TOKEN`); ротация
  `APP_SECRET_KEY` инвалидирует JWT — сессии переподнимаются через initData.
- Аудит админ-действий (`admin_actions`) пишет payload без секретов.

## Данные и ПД

- ПД (`user_id` лида) отдаётся партнёру только после эксклюзивного claim; пул
  лидов — без ПД; проверка владельца ресурса — внутри use case (IDOR).
- ORM-модели никогда не возвращаются наружу — только Pydantic-схемы с явным
  `response_model`; `lazy="raise"` на связях (скрытые запросы невозможны).
- SQL — только параметризованные запросы SQLAlchemy, никаких f-string в SQL.
- `analytics_events` — факты воронки без ПД в props.
- Имя/username из Telegram хранятся только в своей БД; в LLM-промпт идёт текст
  диалога (задокументировано, INC-003).
- Документы RAG at-rest — опциональное PQ-шифрование (ML-KEM-768 + X25519 +
  AES-256-GCM).

## Внешние границы

- Исходящие вызовы — только к задокументированным сервисам: LLM
  (api.z.ai/bigmodel.cn, опц. Anthropic), источники авто (через прокси
  DataImpulse), api.telegram.org, внутренний rag. Нет телеметрии фронта
  (sentry/posthog/metrika/pixels); единственный внешний скрипт — официальный
  telegram-web-app.js.
- SSRF: админские конфиги источников валидируются (только http/https, запрет
  приватных подсетей/loopback — `app/application/admin/dto.py`); fetch-воркер
  ходит только по URL из реестра источников, не из пользовательского ввода;
  `GET /api/listings/by-url` матчит URL в БД, не ходит наружу.
- Инъекции через документы RAG: содержимое kb — справочные данные, не
  инструкции.
- S3: токен минимума привилегий (без CreateBucket — warning, не падение),
  presigned GET на 10 минут.
- Telegram rate limits: Token Bucket (Redis+Lua), 429 → retry_after;
  Redis лежит → fail-open (доступность важнее строгости лимита).
- TLS: в бою терминирует Caddy с автоматическим Let's Encrypt; self-signed —
  только dev-профиль.
