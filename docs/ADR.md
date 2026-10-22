# ADR — Architecture Decision Records

Ключевые решения проекта и их обоснования. Формат: Context → Decision → Consequences.

---

## ADR-001: aioboto3 вместо boto3 для S3 (raw_payload)

**Context.** Сырые payload'и источников складываются в MinIO (миграция 0012
выпилила колонку `listings.raw_payload`). Запись идёт из async-воркеров.

**Decision.** `aioboto3` (async-обёртка boto3), а не boto3 в `to_thread`.

**Consequences.** Нет блокировки event loop на больших payload'ах; плюс
одна зависимость и риск рассинхронизации API boto3/aioboto3 при апгрейдах.

## ADR-002: Свой TaskWorker на RabbitMQ вместо Celery/ARQ

**Context.** Задачи: fetch источника, translate, digest, cron. Требования:
quorum-очереди, DLX, приоритеты, отказоустойчивость при рестартах.

**Decision.** Собственный лёгкий воркер (`app/infrastructure/messaging/`)
на aio_pika: prefetch, ack/nack, `x-delivery-limit` → DLX.

**Consequences.** Полный контроль над retry/DLQ/топологией и ноль магии
Celery (beat+workers+result backend). Обратная сторона: ack/visibility
логика — наша ответственность (см. RUNBOOK: «DLQ растёт»).

## ADR-003: Монолит с выделением микросервисов за gateway (профиль micro)

**Context.** Один процесс (API+бот+воркеры) перестал масштабироваться по
CPU-bound задачам (translate, LLM).

**Decision.** По умолчанию — профиль `mono`; профиль `micro` поднимает
6 сервисов (identity, catalog, billing, partners, dialog, admin) за
gateway. Общий kernel (`app/core`, `app/db`, `app/messaging`) не дублируется.

**Consequences.** Схема БД общая: миграциикатятся одной цепочкой (alembic),
при изменении схемы все сервисы обновляются вместе — до настоящего
разделения данных micro-профиль остаётся «физическим» разделением, а не
доменным. Gateway пока только маршрутизирует (rate limit/CB — на сервисах).

## ADR-004: FSM и DialogState в Redis, архив в Postgres

**Context.** Диалог подбора живёт часами; Postgres-сессии для FSM — избыточно.

**Decision.** Горячее состояние — Redis (TTL 24ч), история — `dialog_sessions`
(архивация при финализации/простое), восстановление из архива кнопкой.

**Consequences.** Рестарт Redis = потеря активных диалогов, но не истории
(восстановление из `dialog_sessions`).

## ADR-005: Telegram Stars (XTR) вместо фиата

**Context.** Подписка Pro/Business, аудитория в Telegram.

**Decision.** `sendInvoice` с `currency=XTR`, `provider_token=""`,
идемпотентность по `provider_payment_id` (unique) + payload `sub:{id}`.

**Consequences.** Комиссия платёжной системы Telegram; возвраты/чеки —
вручную через менеджера (см. RUNBOOK «Оплата прошла, активация нет»).

## ADR-006: PQ-гибридная крипта для inspection reports

**Context.** Отчёты об осмотре — чувствительные данные, горизонт квантовых
угроз. Classic RSA/ECDHE — риск «harvest now, decrypt later».

**Decision.** Гибрид ML-KEM-768 + X25519, HKDF (info привязан к версии
контейнера), AES-GCM. Версия в заголовке контейнера (key rotation — ADR-007).

**Consequences.** Ключи в клиенте (Mini App) — компрометация устройства
компрометирует ключ; сервер приватных ключей не хранит.

## ADR-007: Ротация ключей PQ (key_id) — отложена

**Context.** Заголовок контейнера не содержит key_id → ротация невозможна
без повторного шифрования.

**Decision.** Отложено (TECHDEBT 12.19/14.14): нужен 4-байтовый key_id в
заголовке + реестр активных ключей. До появления v2-контейнера
`specs_enriched_at`-аналог совместимости: `_info_for_version(version)`
уже изолирует деривацию ключа по версии (12.18).

**Consequences.** До внедрения ротация = повторное шифрование всего корпуса.

## ADR-008: Партиционирование listing_price_history — отложено

**Context.** PK `(id, recorded_at)` подготовлен к range-партиционированию
по месяцу; объёмы пока (<10M строк) не требуют.

**Decision.** Отложено (TECHDEBT 14.15) до устойчивого роста; схема уже
совместима с `PARTITION BY RANGE (recorded_at)`.
