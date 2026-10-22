# AI-слой: LLM, агент, RAG, перевод

## LLM-клиент (`app/infrastructure/ai`)

Провайдеры: `glm` (основной, api.z.ai / open.bigmodel.cn), `anthropic`,
`ollama`, `mock` (тесты/dev без LLM). OpenAI-совместимый протокол;
`LLM_BASE_URL` для прокси/Ollama. Ошибки > 5 подряд → переключение на резерв,
дальше — кнопочный режим бота. Контракт — `LLMPort`
(`app/application/shared/ports.py`).

## Агент диалога (`app/application/dialog`)

Диалог подбора авто: извлекает критерии (марка/бюджет/год/…) в
`SearchCriteria`, зовёт tools, отвечает с кнопками. История — 8 последних
сообщений, DialogState в Redis с TTL 24 ч. FAQ-ответы кэшируются (24 ч).

Квоты AI-сообщений — `QuotaStore` (dialog/ports.py), сценарий `quota`.

### Tools (`app/application/dialog` + домен)

- поиск по БД по критериям (сценарий `search`);
- `answer_question` — вопрос в RAG (ниже);
- создание лида (после AI-квалификации: бюджет/срок/готовность к предоплате —
  `Lead.qualification` поднимает цену лида).

AI-поиск из Mini App (`POST /api/ai/message`) — тот же агент
(`app/application/ai_search`).

## RAG-микросервис (`services/rag`)

Локальный RAG на ядре [rag_service](https://github.com/Pavel14701/rag_service)
(monorepo-версия): гибридный поиск (vector + BM25/RRF), агентный retrieval,
multi-provider LLM, DLQ/retry через RabbitMQ.

- `rag_core/` — ядро (domain / application / infrastructure, hexagonal);
- `rag_worker/` — консьюмеры `query_queue`, `ingest_queue`, `delete_queue`;
- `rag_api/` — HTTP :8100 (Bearer `RAG_API_TOKEN`);
- `kb/` — база знаний markdown; ingest: `scripts/ingest_kb.py`.

Клиент в backend (`rag_client`): retry только на сетевые ошибки, circuit
breaker (5 ошибок подряд → пауза 5 мин), таймаут 5 с, кэш ответов в Redis
(`rag:answer:<sha256>`, TTL 1 ч). Confidence ≥ 0.6 → ответ базы знаний;
ниже → ответ LLM; и его нет → «рекомендую уточнить у менеджера».
Промпт-защита: содержимое kb — справочные данные, не инструкции
(анти-инъекции через документы); цифры без источника называть нельзя.

Документы RAG at-rest — опциональное PQ-шифрование (ML-KEM-768 + X25519,
`ENCRYPT_DOCUMENTS`; `app/infrastructure/security/pq_crypto.py`).

## Перевод тайтлов (`services/translate` + `app/infrastructure/translation`)

- NLLB-200-distilled-600M (CPU) на :8200; карта языков
  `TRANSLATE_SOURCE_LANGS` (encar/kcar → `kor_Hang`, che168 → `zho_Hans`).
- Кэш переводов в Redis 30 дней; CB: 3 ошибки → пауза 5 мин + алерт;
  `TRANSLATE_ENABLED=false` → показывается оригинал (fallback обязателен).
- Глоссарий hangul→канон (`translate_glossary.py`): бренды латиницей, модели
  в русской транслитерации — longest-match замена перед NLLB (현대→Hyundai,
  기아→Kia, …), иначе NLLB переводит бренды как нарицательные.
