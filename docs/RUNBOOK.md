# RUNBOOK — эксплуатационные сценарии

Дополняется по итогам инцидентов (см. [INCIDENTS.md](../INCIDENTS.md)).

## DLQ RabbitMQ растёт (`dlq_alert`)

`docker exec auto_bot-rabbitmq-1 rabbitmqctl list_queues name messages
consumers` → посмотреть трейс в логах воркера. Типовые причины:
источник лежит (circuit breaker пропустит сам), job-timeout (encar с
чеками долог — таймаут очереди 360с). Разобрать:
`rabbitmqadmin purge_queue name=dead_letter`.

## Источник не парсится

`GET /api/admin/sources` (health, error_streak). Circuit-breaker
пропускает источник сам;ParserDoctor (fire при 0 карточек) чинит
селекторы. Ручной прогон: `POST /api/admin/sources/{name}/fetch`.

## Centrifugo недоступен

Не инцидент ядра: чаты едут на поллинг-фоллбэке (5с), publish'и
логируются warning. Проверить namespace-конфиг (`configs/centrifugo.json.tmpl`
— namespaces user/partner/thread c subscribe_proxy_enabled+presence),
`docker logs auto_bot-centrifugo-1` (unknown channel = нет namespace).

## Флейк test_rag

`test_answer_question_without_rag_uses_llm_answer` ходит в живой
RAG (localhost:8100) — изолированный прогон зелёный. Не блокер.

## МинIO/photos

503 photos_unavailable = S3 недоступен или креды. Bucket создаётся
лениво; public-read policy идемпотентна. Зеркала фото — фоновая
задача, её сбой не роняет fetch (warning mirror_task_failed).

## Стейл-образы сервисов

Симптом: роут есть в коде, контейнер отдаёт 404 (`grep -c` в
контейнере). Лечение: `docker compose --profile micro build <svc>`.
