# RUNBOOK — эксплуатационные сценарии

Что делать, если... (дополняется по итогам инцидентов — см. INCIDENTS.md)

---

## 🔴 DLQ (`dead_letter`) растёт

**Симптомы.** Алерт `dlq_alert_threshold` (10+ сообщений), `/health/detailed` ок.

**Диагностика.**
1. `python -m app.infrastructure.messaging.replay --limit 100 --dry-run` —
   посмотреть, какие job'ы падают.
2. Логи воркеров: `dlq` после `x-delivery-limit` (5 доставок).

**Действия.**
- Транзиентная причина (сеть, БД): устранить → `python -m
  app.infrastructure.messaging.replay --limit 100` (переотправит в `tasks`).
- Яд не декодируется / job неизвестен: сообщения будут удалены CLI-ем
  как poison (`dlq_poison_message_dropped`) — проверить лог, при
  необходимости вытащить тело из Rabbit management вручную.

## 🔴 RAG недоступен / «circuit_breaker_open»

**Симптомы.** Алерт `cb:rag` (5 ошибок RAG подряд → пауза 5 мин).

**Действия.** Диалог продолжает работать без контекста (fallback в LLM).
1. Проверить `rag_service_url`/`RAG_API_TOKEN`, сам сервис RAG.
2. Breaker полуоткрытого состояния не имеет: после cooldown трафик пойдёт
   сам. Рестарт воркера/API сбрасывает breaker мгновенно (in-memory).
3. Кэш ответов (`rag:answer:*`, TTL 1ч) продолжает отдавать старые ответы.

## 🔴 Источник (encar/kcar) забанил или отдаёт 403

**Симптомы.** `source_skipped_circuit_breaker` в логах fetch-воркера,
`error_streak` в `source_health` растёт, алерт.

**Действия.**
1. Проверить, не сменился ли API источника (ручной curl detail-url).
2. Включить прокси DataImpulse (`PROXY_DATAIMPULSE_USER/PASS`) — источники
   с `capabilities.use_proxy=true` пойдут через шлюз.
3. CB источника открывается после `source_cb_error_streak` (5) ошибок —
   на cooldown; воркер пропускает источник, не вали остальное.

## 🔴 Redis лежит

**Симптомы.** `/health/detailed` → `redis: false`; бот отвечает «Backend
недоступен» на диалогах.

**Действия.** Диалоги теряются (FSM/DialogState в Redis, TTL 24ч), но:
- история восстановима из `dialog_sessions` («Продолжить прошлый диалог»);
- дедуп/квоты fail-open — данные не портятся;
- после рестарта Redis провернуть `digest` вручную не нужно: буферы
  `digest:*` теряются, следующий скан `notify_new_listings` наполнит заново
  (антидубль по sent_log в Postgres).

## 🔴 Оплата прошла, подписка не активировалась

**Симптомы.** Алерт «payment_id=..., user_id=... Нужна ручная активация».

**Действия.**
1. Бот уже ретраил активацию 3 раза (бэкофф 2/4 с) — значит backend был
   недоступен устойчиво.
2. Найти платёж в БД (`payments.provider_payment_id = <charge_id из алерта>`).
3. Повторить активацию: повторный POST `/api/payments/success` идемпотентен
   (unique `provider_payment_id` → 409 = уже активирован).
4. Если платёж в `pending` и Stars списались — активировать вручную и
   завести инцидент в INCIDENTS.md.

## 🔴 Gateway (micro-профиль) недоступен

**Действия.**Mini App и сайт идут через gateway; при его деградации —
откат на профиль `mono` (`docker compose --profile mono up -d`), погасив
gateway micro-профиля (конфликт портов 80/8000).

## 🟡 Медленный LLM (диалоги по 60+ с)

**Действия.** Это норма для reasoning-моделей: `DIALOG_TIMEOUT=120 c` в
`BackendClient`. Если ответы пропадают на таймаутах — проверить провайдера,
при необходимости сменить `LLM_PROVIDER` на запасной в `.env` и рестартовать
API+бота. Повторный ход не задваивается: лок `dlg:turn-lock:{user}` (20 c).

## 🟡 Ротация ключей/секретов

- `APP_SECRET_KEY`/`INTERNAL_TOKEN`: задать новые в `.env`, рестарт всех
  процессов. JWT, выданные старым ключом, инвалидируются (users перелогин).
- PQ-ключи inspection reports: ротация пока не поддержана (ADR-007) —
  компрометация = перевыпуск отчётов.
