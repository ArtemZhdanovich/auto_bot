# AI-контур: LLM-ядро, AI-поиск, анализ цены, отчёт истории, генерация, RAG

> Источник правды — код. Ключевые файлы:
> `app/config.py` (провайдеры/лимиты), `app/infrastructure/ai/`
> (`llm_client.py`, `prompts.py`, `agent.py`, `tools.py`, `rag_client.py`),
> `app/application/dialog/` + `app/application/ai_search/` (AI-поиск),
> `app/application/analysis/` + `app/application/pricing/use_cases.py` +
> `app/domain/pricing/rules.py` (анализ цены),
> `app/application/listings/history_report.py` (отчёт истории),
> `app/application/market/description.py` / `quality.py`,
> `app/application/chat/summary.py`, `app/application/chat/bot.py` +
> `app/infrastructure/services/support_bot.py` (бот поддержки),
> `app/application/admin/assistant.py` (AI-помощник админа),
> микросервисы `services/dialog/`, `services/rag/rag_api/`,
> `services/rag/rag_worker/`, `services/rag/rag_core/`,
> фронт — `frontend/src/lib/listingQuality.ts`, `frontend/src/screens/SellCar.tsx`.

Принцип всего контура: **LLM — «объяснение», доменные правила — «факты»**.
Вердикты, факты, лимиты и суммы считаются детерминированным кодом; LLM
добавляет человеческий текст и обязана падать незаметно: почти у каждой
фичи есть фолбэк (статистика / FAQ / честная заготовка) либо явный 503/429.

---

## 1. Ядро: провайдеры, ключи, клиенты

### 1.1 Конфигурация (`app/config.py`, имена env)

| Env / поле | Дефолт | Смысл |
|---|---|---|
| `LLM_PROVIDER` | `glm` | `glm` \| `anthropic` \| `ollama` \| `mock` |
| `GLM_API_KEY` | — | ключ Zhipu/z.ai (SecretStr); в prod обязателен при `provider=glm` (валидатор `_check_llm_keys` роняет старт без ключа) |
| `GLM_BASE_URL` | `https://api.z.ai/api/paas/v4` | международный эндпоинт; для ключей bigmodel.cn — `https://open.bigmodel.cn/api/paas/v4` |
| `ANTHROPIC_API_KEY` | — | ключ Anthropic |
| `LLM_DEFAULT_MODEL` | `glm-4.6` | рабочая модель |
| `LLM_ADVANCED_MODEL` | `glm-4.6` | «продвинутая» (зарезервировано) |
| `LLM_BASE_URL` | `None` | переопределение (Ollama `http://localhost:11434/v1`, прокси) |
| `LLM_TEMPERATURE` | `0.2` | температура всех вызовов |
| `DIALOG_TTL_SEC` | 86400 | жизнь сессии диалога в Redis |
| `DIALOG_HISTORY_LEN` | 8 | сколько последних сообщений уходит в контекст |
| `FAQ_CACHE_TTL_SEC` | 86400 | кэш FAQ-ответов |
| `RAG_SERVICE_URL` | `None` | пусто = RAG выключен → fallback на LLM |
| `RAG_API_TOKEN` | — | Bearer для rag-сервиса |
| `RAG_ENABLED` | `true` | feature flag rollout |
| `RAG_TIMEOUT_SEC` | 5 | таймаут запроса |
| `RAG_CACHE_TTL_SEC` | 3600 | кэш ответов RAG в Redis |
| `RAG_CONFIDENCE_THRESHOLD` | 0.6 | ниже — fallback на LLM |
| `RAG_CB_FAIL_THRESHOLD` / `RAG_CB_COOLDOWN_SEC` | 5 / 300 | circuit breaker |

Секреты хранятся как `SecretStr` и разворачиваются только в заголовок
(`_secret_or_none`) — в логи/ошибки не попадают.

### 1.2 Клиенты (`app/infrastructure/ai/llm_client.py`)

- `ChatCompletionsClient` — GLM (Zhipu) / Ollama / любое
  `/chat/completions`-совместимое API. `Authorization: Bearer <key>`,
  `tool_choice: auto`, tools в OpenAI-формате.
- `AnthropicClient` — `/v1/messages` (`x-api-key`, `anthropic-version`).
  Ограничение: multi-turn с tools не конвертируется полностью — сообщения
  `role="tool"` отбрасываются (с warning в лог).
- `MockLLMClient` — скриптованный, для тестов и dev без LLM
  (`LLM_PROVIDER=mock`).
- Фабрика `build_llm_client(settings)`; кэш соединений
  `get_cached_llm_client(provider, model)` (`lru_cache`, пул `httpx`):
  не создаёт TLS-handshake на каждый ход; закрытие — в lifespan.

Надёжность (`_post_with_retry`): ретраи на транзиентные статусы
`{408, 425, 429, 500, 502, 503, 504}` и сетевые/таймаут-ошибки; экспоненциальный
backoff 1с → cap 15с; максимум 3 попытки; затем `LLMError`. `LLMResult`
содержит `text`, `tool_calls[]`, `truncated` (обрезка по
`finish_reason in (length, content_filter | max_tokens)`), `finish_reason`.
`tool_calls.arguments` понимается и как dict, и как JSON-строка.

### 1.3 Где живут промпты

Все промпты — константы Python (не файлы):

| Промпт | Файл |
|---|---|
| Системный промпт агента «Артём» | `app/infrastructure/ai/prompts.py::SYSTEM_PROMPT` + `build_messages(state, history_len)` |
| Вердикт цены (строгий JSON) | `app/application/pricing/use_cases.py::SYSTEM_PROMPT` |
| Вывод по истории авто | `app/application/listings/history_report.py::SYSTEM_PROMPT` |
| Копирайтер описания | `app/application/market/description.py::DESCRIPTION_SYSTEM_PROMPT` |
| Выжимка треда | `app/application/chat/summary.py::SUMMARY_SYSTEM_PROMPT` |
| Реранк похожих | `app/application/analysis/similar.py::_system_prompt(limit)` |
| Помощник админа | `app/application/admin/assistant.py::SYSTEM_PROMPT` |

---

## 2. AI-поиск — диалог с агентом

Продуктовая цель: собрать критерии поиска машины в диалоге («хочу кроссовер
до 30к»), показать живые варианты из БД и запустить полный поиск по внешним
источникам с уведомлениями.

Сервис: **dialog-service** (`services/dialog/main.py`, порт 8105) — вынесен
отдельно, чтобы LLM-нагрузка/отказы не влияли на другие контуры. Домены:
`/api/dialog/*` (internal, ходит бот) и `/api/ai/*` (JWT, Mini App).

### 2.1 Состояние и сессии

- Горячее состояние — Redis, ключ `dialog:{user_id}`, TTL `DIALOG_TTL_SEC`
  (24 часа): `DialogState {session_id, status: collecting|ready|searching,
  criteria: SearchCriteria, messages[], turn_count, asked_fields[],
  answered_questions[]}` (`app/infrastructure/cache/dialog_store.py`,
  `app/domain/dialog/state.py`).
- Архив — таблица `dialog_sessions` (Postgres): каждый ход/финализация
  дополнительно архивирует состояние (`SendDialogMessage._archive`, best-effort —
  ошибка архива не ломает ответ).
- Восстановление: `POST /api/dialog/restore` — если активной сессии нет,
  поднимает последнюю из архива (критерии, сообщения, turn_count).
- История: `GET /api/ai/dialog/{session_id}` — только владельцу (IDOR → 404).
- Cleanup-крон сносит архивы, молчащие сутки (`dialog_cleanup_job`).

### 2.2 Один ход агента (`run_agent`, `app/infrastructure/ai/agent.py`)

1. **Сериализация ходов**: `SET NX dlg:turn-lock:{user_id}` c TTL 20с — двойной
   тап не создаёт параллельных ходов (иначе lost update состояния); второй
   запрос получает `DialogError("busy")` → кнопки-fallback.
2. **FAQ без LLM** (`app/domain/dialog/faq.py::match_faq_topic`): вопросные
   сообщения про доставку/таможню/сроки отвечаются из кэша (`answer_faq`,
   TTL `FAQ_CACHE_TTL_SEC`) — 0 токенов.
3. Сообщение пользователя в историю; **ранний выход**: все критичные поля есть
   и текст — чистое подтверждение («да», «давай», «ок», фраза целиком из
   подтверждений ≤24 симв.) → статус `ready` + кнопка «🔍 Искать», LLM не тратится.
4. **LLM + tools**: `llm.complete(build_messages(state, dialog_history_len=8),
   TOOLS)` — system-промпт «Артём» с текущими критериями (`state_json`) +
   последние 8 сообщений (история не дублируется в system — контроль расхода
   токенов).
5. Выполнение tool-calls (валидация каждого патча `apply_patch`).

Инструменты (`app/infrastructure/ai/tools.py`): `update_criteria`,
`ask_clarification` (кнопки только из `FIELD_OPTIONS` — модель не выдумывает
значения), `answer_question`, `lookup_reference`, `preview_search`,
`calc_delivery`, `finalize_search`.

Промпт-правила агента: не больше одного уточнения за раз; не более 7
уточнений подряд; не выдумывать цены (расчёты только через `calc_delivery`,
цифры — только из RAG); страна — только Корея (`country=KR`); критичные поля
для финализации — `make` ИЛИ `model` + `price_max`; реплики «дешевле/плохо
ищешь» трактуются как правка текущих критериев, а не новый поиск.

### 2.3 Квоты и лимиты AI-поиска

- Суточный лимит — из тарифа (`TIERS[tier].ai_daily_limit`: free 3, pro 200,
  business `None`=∞) — `app/application/dialog/use_cases.py::ai_daily_limit`.
- **Списание за выдачу результата, не за сообщение** (`preview_search` с
  вариантами / `finalize_search`), иначе демо-юзер не может пообщаться.
- Счётчик Redis `ai_quota:{user_id}:{YYYYMMDD}` (`app/application/quota/`);
  проверка+списание атомарны (`_quota_check_and_consume`), при падении поиска —
  компенсация `quota_consume(delta=-1)`; статус меняется на `searching` только
  после успешного finalize (иначе зависает).
- Mini App: per-user лимит сообщений `MessageRateLimiter` — Redis INCR+TTL,
  **20 сообщений / 60 сек** (`AI_MESSAGE_RATE_LIMIT`, `app/application/ai_search/`),
  сверх — 429 `rate_limited`; расход квоты логируется (наблюдение).

### 2.4 Финализация поиска по источникам

`finalize_search` (tool) или `POST /api/ai/finalize` / `POST /api/dialog/finalize`:

1. Критерии валидируются (`SearchCriteria`, доменная валидация → 400).
2. `FinalizeDialog`: state → `searching`; критерии сохраняются как **фильтр
   пользователя** (`filters` — дальше работают уведомления/instant/digest).
3. `TriggerFetch`: задачи парсинга ставятся в очередь по **всем включённым
   источникам** (`enqueue_source(name, payload)`); best-effort per source,
   но «не поставлен ни один» → `RuntimeError` и откат квоты.
4. Архив сессии, commit, удаление состояния из Redis.
5. Ответ: `{criteria, sources: ["encar", "kcar", ...]}`; пользователю —
   «Запускаю поиск по источникам. Пришлю уведомления о новых вариантах».

`preview_search` — «мягкий» поиск по БД: пусто по точным критериям →
ослабление второстепенного (цвет → пробег → спеки → годы → цена) и близкие
варианты; `limit` зажат в 1..10 (защита от LLM с limit=1000); аналитика
`EVENT_AI_SEARCH {kind: preview|finalize}`.

### 2.5 API-контракт AI-поиска

| Метод | Путь | Auth | Смысл |
|---|---|---|---|
| POST | `/api/ai/message` | JWT | сообщение агента (rate-limit 20/мин) |
| POST | `/api/ai/reset` | JWT | сброс диалога |
| POST | `/api/ai/finalize` | JWT | зафиксировать поиск (критерии + задачи парсинга) |
| GET | `/api/ai/dialog/{session_id}` | JWT | архивная сессия (только своя) |
| POST | `/api/dialog/message` / `restore` / `reset` / `finalize` | internal | те же сценарии для бота |

Пример `POST /api/ai/message` → 200:

```json
{
  "session_id": "<uuid>",
  "text": "Вот что есть сейчас:\n• Kia K5 2021, 21500000 KRW, 42000 км",
  "options": [],
  "status": "collecting",
  "finalize_ready": false,
  "turn_count": 3,
  "criteria": {"make": "Kia", "model": "K5", "price_max": 25000000, "currency": "KRW", "country": "KR"}
}
```

Ошибки: 429 — лимит сообщений; 503 `ai_unavailable` — LLM не сконфигурирована;
403 — пользователь деактивирован; 404 — пользователя нет.

---

## 3. AI-анализ цены

Продуктовая цель: ответить «справедлива ли цена» по сопоставимым объявлениям;
вердикт должен работать даже без LLM.

Сценарии: `RunAnalysis` (`app/application/analysis/use_cases.py`) — доступ и
квота; `AnalyzePrice` (`app/application/pricing/use_cases.py`) — контекст,
LLM, сохранение; чистые правила — `app/domain/pricing/rules.py`.

### 3.1 Пайплайн

1. Вход: `AnalysisCommand {user_id, listing_id, channel}` → карточка лота.
2. Доступ: гость → 403 (разбор после входа); деактивированный → 403.
3. Квота окна тарифа: `count_recent_user(user_id, since=now - окно)` по ВСЕМ
   машинам суммарно; окна `ANALYSES_WINDOW_DAYS = {free: 7, pro: 1}`,
   лимиты `{free: 1, pro: 30}`, business — без лимита; исчерпана → 403
   (текст квоты).
4. Контекст рынка: сопоставимые той же модели ±2 года, до 30 шт.
   (`COMPS_LIMIT` в `app/infrastructure/db/repositories/price_analysis.py`)
   → `market_stats` (min/max/median; <2 шт. → `None`) + история цены объявления.
5. «Глубокий» разбор (Business/тарифы с `inspection`): выжимка чека осмотра
   (`_inspection_digest`: grade, summary ≤400 симв., повреждения ≤8 заметок)
   подмешивается в промпт — цена объясняется с учётом состояния кузова.
6. LLM: строгий JSON `{"verdict": "deal|fair|overpriced", "fair_min", "fair_max",
   "summary", "factors[≤4]"}`; парсер `parse_verdict_json` терпит markdown-заборы
   и мусор, но пропускает только известные вердикты (CHECK в БД).
7. Сведение: LLM не ответила/недоступна → статистика: вердикт по порогам
   (`price/median <= 0.93 → deal`, `>= 1.07 → overpriced`, иначе `fair`;
   без рынка — `fair`), диапазон `median*0.93..median*1.07`, фактор
   «N сопоставимых: min–max, медиана M», `source: "stats"` вместо `"ai"`.
8. Сохранение: таблица `price_analyses` (миграция 0015) — вердикт, диапазон,
   summary, полный payload JSONB, `user_id`, `created_at`; индекс
   `(listing_id, created_at)`.

### 3.2 API

- `POST /api/listings/{id}/analysis` (JWT, Mini App; internal-вариант для
  бота — тот же сценарий): 200 — результат; 403 — гость/квота; 404 — лот.
- `GET /api/listings/{id}/analysis` — кэш: последний payload по листингу
  (общий на объявление, `GetLatestAnalysis.latest_payload`, без LLM).

Пример ответа (сокращённо):

```json
{
  "listing_id": 789,
  "verdict": "deal",
  "price": 21500000, "currency": "KRW",
  "fair_min": 20500000, "fair_max": 23500000,
  "summary": "Цена ниже медианы рынка на ~8% …",
  "factors": ["…до 4 факторов…"],
  "comps": {"count": 14, "min": 21000000, "max": 26000000, "median": 23400000},
  "history": [{"price": 22500000, "recorded_at": "<ISO>"}],
  "source": "ai", "created_at": "<ISO>"
}
```

### 3.3 Похожие машины (`app/application/analysis/similar.py`)

SQL-кандидаты (та же марка/модель, ±3 года, цена ±35%, до 24 шт.) → LLM-реранк
топ-N со строгим JSON `{items:[{id, reason, score 0..1}]}`; сбой модели →
фолбэк по близости цены, `source: "stats"`, причина
«Похожие марка/модель, год и цена». Ручка `POST /api/listings/{id}/similar`
(используется кнопкой «Ищем похожие» после продажи машины из избранного).

---

## 4. AI-отчёт истории лота (ДТП)

Продуктовая цель: из «сырого» чек-осмотра encar сделать структурный отчёт
истории машины для покупателя. `app/application/listings/history_report.py`.

### 4.1 Источник данных

История ДТП живёт **текстом** в `inspection_reports.summary` (адаптер encar
складывает туда факты record/open: «история ДТП: N страховых случаев …»,
«ДТП 2023-03-09 — выплата 890 800 원»). Старые отчёты прошли машинный перевод
и хранят те же строки по-английски — парсер понимает оба формата.

### 4.2 Пайплайн (`BuildHistoryReport`, GET /api/listings/{id}/history-report)

1. Доступ: гость → 403 `history_guest_forbidden`; лот/юзер → 404.
2. Кэш Redis `history-report:{listing_id}`, TTL **24 часа** (факты меняются
   редко); лежачий/испорченный кэш не роняет отчёт (best-effort).
3. Детерминированный факт-парсер `collect_history_facts` (regex-набор):
   - счётчик ДТП + разбивка вины (`ACCIDENT_TOTAL_RE`, `MY_FAULT_RE` /
     `OTHER_FAULT_RE` — «по этому авто: 2» / «по вине других: 1» и переводы);
   - «страховых случаев нет» (`ACCIDENT_NONE_RE`);
   - строки отдельных ДТП: дата (`ACCIDENT_ENTRY_RE`) + выплата
     (`PAYOUT_RE`) → последнее ДТП `{date, description: "страховая выплата ≈N ₩"}`;
   - смен владельцев (`OWNERS_COUNT_RE`), первая регистрация (`FIRST_REG_RE`,
     `20230828 → 2023-08-28`), коммерческое/гос использование, «Clean Encar»,
     «вердикт Encar: без ДТП», флаг ДТП чека осмотра.
4. Факты: `{car, accidents{count, by_this_car, by_others, last}, mileage_confirmed
   (есть чек), owner_type, owner_history[], inspection{grade, inspected_at,
   marks_count, issues[]}}`. Нет ни чека, ни owner-истории → 404
   `no_history_data`.
5. LLM-абзац: системный промпт «только факты из входа, 2-3 предложения,
   без markdown»; сбой модели → отчёт без абзаца (факты остаются).
6. Кэширование payload в Redis, ответ `HistoryReportView {listing_id, facts,
   llm_summary, generated_at}`.

Ограничения: парсер — эвристика по формату строк encar (+ машиноперевод);
качество зависит от полноты `summary`; кэш не инвалидируется раньше TTL.

---

## 5. Генерация описания лота (SellCar «✨ Сгенерировать»)

Продуктовая цель: продающее описание из полей черновика за один тап.
`app/application/market/description.py`, ручка
`POST /api/market/description` (`app/transport/http/routers/market.py`).

Пайплайн:

1. Доступ: гость → 403; профиль → 404.
2. Лимит free-тарифа: **5 генераций/день** (Redis INCR+EXPIRE, окно 24ч,
   ключ `chat:rl:desc:{uid}`); сверх — **429** `ai_generation_limit`;
   лежачий Redis не блокирует генерацию (fail-open). Pro/Business — без лимита.
3. LLM: системный промпт-гварды — «только факты из входных данных, 400-800
   знаков, без обещаний/гарантий/markdown/эмодзи»; user-промпт собирается из
   полей визарда (марка, модель, год, цена+валюта, пробег, топливо, КПП,
   число фото).
4. Ответ: `{text: "…"}`; пустой ответ модели (<40 симв.) → 400
   `description_generation_failed`; итог обрезается до 2000 симв. (лимит
   `owner_description`).

Фронт: `frontend/src/screens/SellCar.tsx` — кнопка «✨ Сгенерировать
описание» на шаге описания; сгенерированный текст попадает в textarea
(пользователь правит перед публикацией); клиентские проверки качества —
`localQualityChecks` (см. §6).

---

## 6. Качество объявления

Продуктовая цель: до публикации показать продавцу, что объявление слабое, и
дать конкретные советы. Без LLM — мгновенно и бесплатно.

Бэк — чистая функция `listing_quality` (`app/application/market/quality.py`):

| Правило | Штраф | Совет |
|---|---|---|
| нет фото | −30 | «Добавьте фотографии — объявления без фото почти не смотрят» |
| фото < 3 | −25 | «Добавьте фото кузова и салона (минимум 3)» |
| описание < 80 симв. | −20 | «Дополните описание — минимум 80 знаков» |
| цена вне ±20% медианы модели | −15 | «Цена выше/ниже рынка (~M)» |

Score = `100 − штрафы`, зажим в 0..100; медиана — тот же контекст
сопоставимых, что у AI-разбора (`comps_median`, best-effort: ошибкам рынка
ошибки не роняют my-lots). Возвращается `{score, tips}`:

- при создании/submit — `command_quality` по полям формы (в ответе
  `POST /api/market/lots`, поле `quality`);
- в `GET /api/market/my-lots` и `PATCH /lots/{id}` — `lot_quality` по снимку
  лота (фото = `images`, описание = `owner_description`).

Фронт — `frontend/src/lib/listingQuality.ts`:

- `localQualityChecks(photosCount, description)` — мгновенные клиентские
  чеки на визарде SellCar (без сети): «Фото — N из 3», «Описание ≥ 80 знаков»;
  авторитетный score с правилом цены приходит с бэка (комментарий модуля
  прямо фиксирует: клиент — только превью, бэк — источник правды).
- `qualityTone(score)` — тон пилюли: `>=80` «deal» (зелёный), `>=50` «warn»,
  ниже «danger».

---

## 7. Выжимка диалогов (admin/partner)

Продуктовая цель: оператор/партнёр заходит в тред и за 5 секунд понимает
контекст. `app/application/chat/summary.py`, ручки
`POST /api/admin/chats/{thread_id}/summary` и
`POST /api/partner/chats/{thread_id}/summary`.

Пайплайн (`SummarizeThread`):

1. Доступ: партнёр — только свои треды (`get_participant_thread`, чужой → 404);
   админ — все треды.
2. Хвост переписки: последние **50** сообщений по возрастанию id
   (`SUMMARY_MESSAGES_LIMIT`); пусто → 404 `chat_summary_empty`.
3. Кэш Redis `chat:summary:{thread_id}:{last_message_id}`, TTL **1 час** —
   новое сообщение меняет `last_message_id` и естественным образом сбрасывает
   ключ.
4. LLM: системный промпт «ассистент менеджера автосалона, максимум 600
   знаков: что хочет клиент, какие лоты обсуждаются, сомнения,
   договорённости, следующий шаг менеджера; только по переписке».
5. Ответ обрезается до 600 симв.: `{thread_id, summary, last_message_id,
   generated_at}`.

Ограничение: фолбэка нет — сбой LLM даёт 503 `ai_unavailable` (выжимка без
модели невозможна, в отличие от разбора цены).

---

## 8. RAG-бот поддержки и AI-помощник админа

### 8.1 Бот поддержки (support-чаты)

`app/application/chat/bot.py::ReplySupportBot` + адаптеры
`app/infrastructure/services/support_bot.py`:

1. Триггер: сообщение юзера в support-треде → `SessionSupportBotRunner.spawn`
   (фоновая задача на своей сессии — REST-ответ не ждёт генерации).
2. Молчит, если тред не support / закрыт / `bot_paused` (оператор писал
   последним — флаг снимает новое сообщение юзера).
3. Лимит free: **15 сообщений бота/сутки** (`SUPPORT_FREE_DAILY_LIMIT`,
   окно 24ч по `count_messages(sender_type='user')`); при превышении — не 403,
   а апселл-сообщение о Pro/Business (чат остаётся рабочим). Pro/Business —
   безлимит.
4. Событие `typing {user_type: 'bot'}` в личные каналы участников (RAG-лаг
   секундами).
5. Ответ: **RAG** (`SupportRagAdapter.ask`, контекст `{tenant: "autoselect",
   scope: "support"}`, confidence ≥ порога) → пусто/недоступно → **LLM по
   истории треда** (`SupportLLMAdapter`) → честная заготовка
   (`SUPPORT_FALLBACK`).

### 8.2 AI-помощник админа (`app/application/admin/assistant.py`)

Tool-loop (до `MAX_STEPS=3` вызовов инструментов, история 8 сообщений):
`leads_stats`, `partners_list`, `sources_health` — только чтение; админ-действия
модель не выполняет (подсказывает, где в панели). Фолбэк — «переформулируйте
вопрос».

---

## 9. RAG-сервис поддержки (services/rag)

Назначение: база знаний по таможне/доставке/рискам/сборам/FAQ (Markdown-документы
`services/rag/kb/*.md`) с поиском и генерацией ответа; отвечает и диалоговому
агенту (`answer_question`), и боту поддержки, и (топологиями) внешним
потребителям.

### 9.1 Архитектура

```
 auto_bot (RAGClient, infrastructure/ai/rag_client.py)
    │  POST {rag_service_url}/api/v1/ask  (Bearer RAG_API_TOKEN)
    │  {question, context:{tenant, user_id, filters:{topic}}}
    ▼
 rag_api (services/rag/rag_api/main.py, FastAPI, :8100)
    │  минтует JWT (sub=SERVICE_USER_ID, groups=SERVICE_GROUPS, TTL=ASK_TIMEOUT+60)
    │  публикует RPC в query_queue (reply_to=exclusive-очередь, correlation_id)
    ▼
 rag_worker (services/rag/rag_worker/, RabbitMQ-консюмеры)
    │  QueryConsumer → RetrieverService.answer_query (rag_core)
    │      поиск в Qdrant (коллекция documents; опц. гибрид BM25+RRF)
    │      → LLM-генерация по чанкам (провайдер rag-сервиса, свой ключ)
    │  ответ в reply_to: {query_id, answer, sources[]}
    ▼
 rag_api → {answer, sources[{doc_id, chunk_id, score, title, snippet}], confidence, latency_ms}
```

Очереди воркера: `ingest_queue` (индексация документа), `delete_queue`,
`query_queue` (RPC), `reindex_queue`. Надёжность: DLX+DLQ на каждую очередь,
ретраи с экспоненциальным backoff через TTL-очереди (5с/30с/120с),
идемпотентность по `message_id` в Redis, poison-сообщения (PermanentError) —
сразу в DLQ.

### 9.2 Поиск и индексация (rag_core)

- Векторное хранилище — **Qdrant** (`QDRANT_HOST:6333`, gRPC prefer, опц.
  `QDRANT_API_KEY`/https); коллекция по умолчанию **`documents`**; HNSW-тюн
  (`QDRANT_HNSW_*`); blue-green reindex через атомарный флип алиаса.
- Эмбеддинги: `intfloat/multilingual-e5-small` (префиксы `query: ` /
  `passage: `), батчи, кэш запросов (TTL 1ч).
- Гибридный поиск: BM25-кандидаты + векторные хиты, слияние RRF
  (`SEARCH_HYBRID`, k=60, кандидаты 50); пороги отсечения; бюджет контекста
  LLM `SEARCH_CONTEXT_MAX_CHARS=12000`.
- Доступ: Qdrant-фильтр `owner_id == user OR access_group IN groups` (группы
  из JWT-claim или таблицы `user_groups`).
- LLM воркера: свой провайдер/ключ (`RAG_LLM_PROVIDER=glm`,
  `RAG_LLM_MODEL=glm-4.6`, `LLM_MAX_TOKENS=500`, temperature 0.1,
  circuit breaker 5 ошибок/60с); per-request роутинг с allowlist'ами
  (`LLM_ENABLED_PROVIDERS`, `LLM_ALLOWED_MODELS`).
- Хранение: Postgres (rag-схема, документы/разговоры), MinIO (файлы),
  опциональные Redis-кэши, PII-редакция разговоров, пост-квантовое шифрование
  документов (ML-KEM-768, флаг).

### 9.3 Клиент auto_bot (`RAGClient`)

- Кэш Redis `rag:answer:{sha256(question.lower()+context)}` TTL 1ч; per-user
  ключи контекста (`user_id` и т.п.) исключены из ключа — одинаковый вопрос
  переиспользует ответ.
- Ретраи: только сеть/5xx (2 попытки, backoff с jitter); 4xx — клиентская
  ошибка, breaker не трогается. Circuit breaker in-memory per-process:
  5 ошибок подряд → пауза 300с + алерт «ответы через LLM».
- `enabled=False`, если `RAG_SERVICE_URL` пуст или `RAG_ENABLED=false` — все
  вызовы сразу в fallback LLM.
- Потребители: агент диалога `_ask_rag` (темы `customs|delivery|risks|fees|faq`;
  confidence < 0.6 или без confidence → fallback на ответ LLM; при полном
  отсутствии — «уточните у менеджера»), бот поддержки (§8.1).

---

## 10. Сводная таблица AI-фич

| Фича | Ручка | LLM | Фолбэк | Хранение результата | Лимиты |
|---|---|---|---|---|---|
| AI-поиск (диалог) | `/api/ai/message`, `/api/ai/finalize` | glm-4.6 + tools | FAQ, кнопки, статистика | Redis `dialog:*`, архив `dialog_sessions`, фильтры | 3/200/∞ в день за результат; 20 сообщ/мин (Mini App) |
| Анализ цены | `POST /api/listings/{id}/analysis` | + контекст рынка/чека | статистика (`source=stats`) | `price_analyses` (0015) | 1/нед, 30/день, ∞ |
| Похожие | `POST /api/listings/{id}/similar` | реранк | близость цены | не хранится (ответ) | — |
| Отчёт истории | `GET /api/listings/{id}/history-report` | абзац-вывод | факты без абзаца | Redis 24ч | гость 403 |
| Генерация описания | `POST /api/market/description` | копирайтер | нет (400/429/503) | не хранится (уходит в draft) | free 5/день |
| Качество объявления | в `my-lots`/`create` | нет (правила) | — | вычисляется на лету | — |
| Выжимка треда | `POST /api/{admin\|partner}/chats/{id}/summary` | выжимка | нет (503) | Redis 1ч | 50 сообщ., ≤600 зн. |
| Бот поддержки | сообщения support-треда | RAG → LLM | заготовка | сообщения чата | free 15/день |
| Помощник админа | `/api/admin/assistant/*` | + 3 tools | текст-фолбэк | история админа | 3 шага tool-loop |
| RAG-сервис | `POST /api/v1/ask` (внутр.) | glm воркера | breaker → LLM auto_bot | Postgres/MinIO/Qdrant | breaker 5/300с |

Стоимость/троттлинг: прямой биллинг токенов в коде не считается; бюджет
защищается структурно — квоты за результат, rate-limit сообщений, FAQ-кэш и
кэши ответов (RAG 1ч, история 24ч, выжимка 1ч), ретраи с backoff и circuit
breaker на каждом внешнем вызове, `llm_max_tokens=500` в rag-воркере.
