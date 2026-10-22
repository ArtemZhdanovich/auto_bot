# ТЕХДОЛГ: план на 14 этапов

> **Принципы:**
> - Сначала — деньги, безопасность, данные. Потом — «тихие» баги (тесты зелёные, фича мертва). Потом — гигиена. В конце — архитектурные улучшения.
> - Один фикс = один PR = один сценарный тест (не «функция вернула X», а «при падении Y система делает Z»).
> - Перед каждым `commit` — вопрос: «кто ещё может писать сюда параллельно?».
> - Каждый PR не ломает `pytest -q`, `ruff`, `mypy`, `tsc`.
>
> **Структура:** 12 этапов точечных фиксов + 2 этапа архитектурных улучшений (13 — очереди, критично; 14 — общая эволюция).


> **Статус на 2026-10-22** (ревизия после рефакторинга Clean Architecture / Dishka / FastStream):
> из **156** пунктов — ✅ закрыто **107**, 🟡 частично **27**, ⬜ открыто **22**.
> Статусы проставлены в колонке «Статус» каждой таблицы; формулировка проблемы и фикса сохранена как в оригинале
> (пути к файлам могут указывать на старую структуру `app/core`, `app/services`, `app/workers` — код теперь в
> `app/domain`, `app/application/<сервис>`, `app/infrastructure`, `app/transport`).

---

## Этап 1. Безопасность и аутентификация

> **Статус 22.10 (после дожима):** ✅ 9 / 🟡 0 / ⬜ 0 из 9.

**Цель:** закрыть дыры, которые нельзя оставлять в проде ни дня.
**Критерий готовности:** `APP_SECRET_KEY=""` не стартует; JWT по коду нельзя забрать из чужого браузера; `state.criteria` не инжектит промпт.

| # | Файл | Проблема | Severity | Фикс | Тест | Статус |
|---|---|---|---|---|---|---|
| 1.1 | `core/deps_internal.py` | Пустой `INTERNAL_TOKEN` + пустой `APP_SECRET_KEY` → `compare_digest("", "")` = True → открытый internal API | **Critical** | `internal_token()` бросает `HTTPException(503)` если результат пустой; в `_prod_secrets_required` добавить `if not self.app_secret_key: raise` | `test_internal_token_empty_rejected` | ✅ **ЗАКРЫТО** — internal_token() → 503 fail-closed (deps/internal.py); _prod_secrets_required: запрет дев-секрета, длина <32, INTERNAL_TOKEN обязателен в prod (INC-004) |
| 1.2 | `api/auth.py` | `GET /telegram/link/{code}` отдаёт JWT по одному `code` без привязки к браузеру | **Critical** | HttpOnly-cookie `state` при `POST /link`; `GET` проверяет совпадение. Или отдельный `poll_token`, возвращаемый только создателю ссылки | `test_login_code_bound_to_session` | ✅ **ЗАКРЫТО** — state-cookie tglogin_state (SameSite, path-скоуп) + LoginStateMismatch → 401 в transport/http/routers/auth.py |
| 1.3 | `ai/prompts.py` + `schemas/criteria.py` | Prompt injection: `make: "Игнорируй правила..."` попадает в system prompt без ограничений | **High** | `constr(max_length=100, pattern=r"^[^\n\r]*$")` на строковые поля `SearchCriteria` | `test_criteria_rejects_newlines_and_long_strings` | ✅ **ЗАКРЫТО** (22.10, эта серия) — фикс + тест в коде |
| 1.4 | `api/auth.py` | `auth_guest` без rate limit — скрипт создаёт 6000 гостей/мин | **High** | Rate limit по IP (Redis fixed window, 5/мин) | `test_guest_rate_limited` | ✅ **ЗАКРЫТО** (22.10, эта серия) — фикс + тест в коде |
| 1.5 | `config.py` | `app_env: str` — `APP_ENV=production` обходит fail-fast | **Medium** | `Literal["dev","staging","prod"]` для `app_env`, `llm_provider`, `jwt_algorithm` | `test_app_env_literal` | ✅ **ЗАКРЫТО** — Literal для app_env, jwt_algorithm, llm_provider, log_level |
| 1.6 | `config.py` | `database_url`/`rabbitmq_url` дефолты `localhost` не проверяются в prod | **Medium** | В `_prod_secrets_required`: сравнить с `model_fields[...].default` | `test_prod_rejects_default_db_url` | ✅ **ЗАКРЫТО** (22.10, эта серия) — фикс + тест в коде |
| 1.7 | `config.py` | `raw_payload_enabled=True` без `s3_endpoint_url` не валидируется | **Medium** | `@model_validator`: `if raw_payload_enabled and not s3_endpoint_url: raise` | `test_feature_flag_requires_dep` | ✅ **ЗАКРЫТО** — model_validator: S3_ENDPOINT_URL и креды обязательны при RAW_PAYLOAD_ENABLED=true |
| 1.8 | `api/auth.py` | Мёртвый код после `raise` в `_authenticate_via_init_data` | Low | Удалить дубликат | — | ✅ **ЗАКРЫТО** — устранено рефакторингом: auth переписан (dishka-провайдеры, InternalDishkaRoute), мёртвого кода нет |
| 1.9 | `api/analysis.py` | `_is_web_guest` — эвристика `telegram_id < 0 and email is None` | Low | Явный флаг `is_guest` в модели | — | ✅ **ЗАКРЫТО** — явное свойство is_guest у UserProfile (application/dialog/dto.py) |

---

## Этап 2. Деньги: атомарность claim и биллинга

> **Статус 22.10 (после дожима):** ✅ 10 / 🟡 0 / ⬜ 0 из 10.

**Цель:** исключить двойное списание и потерю денег.
**Критерий готовности:** два параллельных `claim_lead` одного лида → один `ok`, второй `taken`; двойное списание за один лид невозможно на уровне БД.

| # | Файл | Проблема | Severity | Фикс | Тест | Статус |
|---|---|---|---|---|---|---|
| 2.1 | `services/partners.py:claim_lead` | `session.get(Lead)` + проверка + запись — без `FOR UPDATE` → два партнёра забирают один лид | **Critical** | `SELECT ... WHERE id=:id AND partner_id IS NULL FOR UPDATE` + проверка `rowcount`; баланс списывать в той же транзакции | `test_claim_lead_concurrent_only_one_wins` | ✅ **ЗАКРЫТО** — claim_lead_row: UPDATE … WHERE partner_id IS NULL RETURNING — атомарно |
| 2.2 | `services/partners.py:claim_batch` | То же для батча | **Critical** | `UPDATE leads SET partner_id=... WHERE id=ANY(:ids) AND partner_id IS NULL RETURNING *`, списывать `cost * len(returned)` | `test_claim_batch_concurrent` | ✅ **ЗАКРЫТО** — claim_batch_rows тем же паттерном (RETURNING) |
| 2.3 | `services/partners.py:_charge` | `account.balance = balance - cost` — read-modify-write в Python, не атомарно | **High** | `UPDATE partner_accounts SET balance = balance - :cost WHERE id=:id AND balance >= :cost RETURNING balance` | `test_charge_atomic` | ✅ **ЗАКРЫТО** — update_balance_returning: balance = balance + delta в SQL, balance_after из RETURNING, журнал в той же транзакции |
| 2.4 | `db/models.py:PartnerTransaction` | Нет unique `(lead_id, kind)` → двойное списание за один лид не поймать | **High** | `UniqueConstraint("lead_id","kind")` (partial `WHERE kind='charge_lead'`) | `test_unique_charge_per_lead` | ✅ **ЗАКРЫТО** (22.10, эта серия) — фикс + тест в коде |
| 2.5 | `db/models.py:PartnerAccount` | `_round2(value + 1e-9)` — костыль на `float` | **Medium** | `Decimal` везде (см. этап 12), убрать `1e-9` | `test_balance_decimal_arithmetic` | ✅ **ЗАКРЫТО** — Decimal + Numeric(12,2) повсюду (PartnerAccount.balance), float-костыль ушёл |
| 2.6 | `db/models.py:Payment` | `provider_payment_id` без unique | **Medium** | `UniqueConstraint("provider_payment_id")` (partial `WHERE provider_payment_id IS NOT NULL`) | `test_provider_payment_id_unique` | ✅ **ЗАКРЫТО** — uq_payments_provider_payment_id (provider, provider_payment_id) |
| 2.7 | `api/payments.py:payment_success` | Нет проверки, что `provider_payment_id` не использован другим платежом | **Medium** | Ловить `IntegrityError` → 409 | `test_payment_success_rejects_duplicate_charge` | ✅ **ЗАКРЫТО** (22.10, эта серия) — фикс + тест в коде |
| 2.8 | `api/payments.py:prepare_payment` | `period_days` не валидируется в `(30, 365)` | **Medium** | `Literal[30, 365]` в `PaymentPrepareIn` | `test_prepare_payment_rejects_weird_period` | ✅ **ЗАКРЫТО** — StarsInvoiceRequest: tier Literal[pro,business], period Literal[month,year] |
| 2.9 | `bot/handlers.py:on_successful_payment` | Backend down → оплата прошла, подписка не активирована | **High** | Enqueue `payment_activate` в очередь `send` с retry; или webhook на backend | `test_payment_activation_retried` | ✅ **ЗАКРЫТО** (22.10, эта серия) — фикс + тест в коде |
| 2.10 | `bot/handlers.py:sub_buy` | Двойной клик → два pending-платежа | **Medium** | Блокировать кнопку до ответа (FSM-флаг) или backend чистит старые pending | `test_double_click_single_pending` | ✅ **ЗАКРЫТО** (22.10, эта серия) — фикс + тест в коде |

---

## Этап 3. Данные: дедуп, индексы, потеря объявлений

> **Статус 22.10 (после дожима):** ✅ 8 / 🟡 0 / ⬜ 0 из 8.

**Цель:** остановить тихую потерю объявлений и убрать seq scan с горячих путей.
**Критерий готовности:** повторный `is_duplicate` с тем же VIN не «отравляет» ключ при возврате `True`; `EXPLAIN` на ленту показывает `Index Scan`.

| # | Файл | Проблема | Severity | Фикс | Тест | Статус |
|---|---|---|---|---|---|---|
| 3.1 | `adapters/common/dedup.py:is_duplicate` | Ставит ключи при возврате `True` → VIN «отравлен» | **High** | Сначала `EXISTS`/`MGET` на все ключи, ставить `SET NX` только если ни один не найден | `test_is_duplicate_no_side_effect_on_true` | ✅ **ЗАКРЫТО** — дедуп переписан на Lua claim/release (_CLAIM_SCRIPT/_RELEASE_SCRIPT): ключи ставятся атомарно, есть откат |
| 3.2 | `adapters/common/dedup.py:fuzzy_key` | Включает `price`/`mileage` → смена цены = новый листинг | **Medium** | Убрать `price` из fuzzy, округлить `mileage_km` до 1000 | `test_fuzzy_key_ignores_price` | ✅ **ЗАКРЫТО** (22.10, эта серия) — фикс + тест в коде |
| 3.3 | `db/models.py:Listing` | `parsed_at` без индекса — главный `ORDER BY` ленты = seq scan | **High** | `Index("idx_listings_parsed_at", "parsed_at")` | `test_explain_list_listings_uses_index` | ✅ **ЗАКРЫТО** — idx_listings_active_source_parsed (is_active, source, parsed_at) под ленту |
| 3.4 | `db/models.py:UserFilter` | `user_id` без индекса — `list_filters` seq scan | **Medium/High** | `Index("idx_filters_user", "user_id")` | `test_explain_list_filters_uses_index` | ✅ **ЗАКРЫТО** — idx_filters_user_created (user_id, created_at) |
| 3.5 | `db/models.py` | `Lead.user_id`, `Lead.partner_id`, `PartnerApiKey.partner_id`, `Payment.user_id`, `DialogSession.user_id`, `DialogSession.updated_at` без индексов | **Medium** | Добавить индексы под конкретные запросы | `test_explain_*` | ✅ **ЗАКРЫТО** — idx_leads_user/partner_created, idx_payments_user_created, idx_dialog_sessions_user/updated, idx_partner_tx_partner_created |
| 3.6 | `db/models.py:DialogSession` | `id: UUID` без `default` — каждый insert обязан явно ставить `id` | **High** | `default=uuid.uuid4` + `server_default=func.gen_random_uuid()` | `test_dialog_session_autogenerates_id` | ✅ **ЗАКРЫТО** (22.10, эта серия) — фикс + тест в коде |
| 3.7 | `services/fetch.py:enrich_listing_specs` | Enrich на каждый GET карточки → внешний запрос к encar при каждом PDP | **High** | Флаг `specs_enriched_at`; enrich только если `NULL` или старше N дней | `test_enrich_once_per_listing` | ✅ **ЗАКРЫТО** (22.10, эта серия) — фикс + тест в коде |
| 3.8 | `api/listings.py:get_listing_by_url` | `ilike("%needle%")` (full scan) + `scalar_one_or_none` → `MultipleResultsFound` → 500 | **High** | Нормализованный хвост в отдельной колонке + индекс; `limit(1)` | `test_get_listing_by_url_no_500_on_multiple` | ✅ **ЗАКРЫТО** — by-url: 0 → 404, >1 → 409 — MultipleResultsFound/500 невозможен |

---

## Этап 4. AI-агент: промпты, tools, диалог

> **Статус 22.10 (после дожима):** ✅ 9 / 🟡 2 / ⬜ 0 из 11.

**Цель:** убрать мёртвые фичи и потерю интента в диалоге.
**Критерий готовности:** `_is_confirmation` не съедает «всё давай BMW»; несколько tool-calls не затирают ответ; `finalize_search` не списывает квоту впустую.

| # | Файл | Проблема | Severity | Фикс | Тест | Статус |
|---|---|---|---|---|---|---|
| 4.1 | `ai/agent.py:_is_confirmation` | `startswith("всё ")` ловит «всё давай BMW» → LLM не вызывается, интент потерян | **High** | Только точное совпадение (`==`) или проверка «после отрезания confirm-слова не осталось содержательных токенов» | `test_confirmation_does_not_eat_intent` | ✅ **ЗАКРЫТО** — _is_confirmation: точный матч по набору коротких подтверждений, «всё давай BMW» не теряется |
| 4.2 | `ai/tools.py:finalize_search` | Квота списывается даже если `ctx.finalize is None`; нет гейта `has_critical_fields` | **High** | Consume только при успешном `finalize`; `if not has_critical_fields(...): return ToolOutcome(...)` | `test_finalize_without_critical_fields` | ✅ **ЗАКРЫТО** — гейт has_critical_fields перед finalize; квота списывается до поиска с компенсацией delta=-1 при неудаче |
| 4.3 | `ai/tools.py:apply_patch` | `ValidationError` → внешний «Что-то пошло не так» вместо «Не понял значение X» | **High** | `try/except ValidationError` внутри, вернуть `ToolOutcome(text="Не понял значение для X")` | `test_apply_patch_validation_error_message` | ✅ **ЗАКРЫТО** — ValidationError ловится внутри tools и превращается в понятный ToolOutcome |
| 4.4 | `ai/tools.py:apply_patch` | `model_copy(update=valid)` не перевалидирует cross-field валидаторы | **Medium** | `SearchCriteria.model_validate({**state.criteria.model_dump(), **valid})` | `test_apply_patch_cross_field_validation` | 🟡 **ЧАСТИЧНО** — model_validate в apply_patch есть; cross-field сценарий тестом не покрыт |
| 4.5 | `ai/tools.py:lookup_reference` | Подстрока: `"ai" in "hyundai"` → Hyundai | **Medium** | `len(q) >= 3` + `startswith` или Levenshtein | `test_lookup_reference_no_false_positive` | ✅ **ЗАКРЫТО** (22.10, эта серия) — фикс + тест в коде |
| 4.6 | `ai/agent.py` | Несколько tool-calls — выживает только последний текст | **Medium** | Приоритет: answer_question > preview > update_criteria; или конкатенация | `test_multiple_tools_preserve_answer` | ✅ **ЗАКРЫТО** — приоритет answer_question при нескольких tool-calls |
| 4.7 | `ai/agent.py` | `int(args.get("limit"))` падает на `limit=null` | **Medium** | `_to_int(value, default)`; клампить `limit` в 1..10, `power_hp` в 0..2000 | `test_preview_search_null_limit` | ✅ **ЗАКРЫТО** — _safe_int + клампы min/max на limit и числовые аргументы |
| 4.8 | `ai/prompts.py:build_messages` | Сообщения дублируются в system-промпте и как turns | **Medium** | Убрать `messages_str` из system, оставить только `state_json` | — | ✅ **ЗАКРЫТО** (22.10, эта серия) — фикс + тест в коде |
| 4.9 | `ai/agent.py:run_agent` | `state.messages` unbounded → Redis растёт | **Medium** | Обрезать до 50 при save | `test_state_messages_capped` | ✅ **ЗАКРЫТО** (22.10, эта серия) — фикс + тест в коде |
| 4.10 | `ai/agent.py:run_agent` | `store.load` → mutate → `store.save` без лока → lost update при двойном тапе | **Medium** | `SET NX` лок на `user_id` на время хода или `WATCH/MULTI` | `test_dialog_state_no_lost_update` | ✅ **ЗАКРЫТО** (22.10, эта серия) — фикс + тест в коде |
| 4.11 | `ai/agent.py:run_agent` | Нет correlation id — логи разрознены | **Medium** | `trace_id` в каждом логе (`extra={"trace_id": ...}`) | — | 🟡 **ЧАСТИЧНО** — trace_id для HTTP (TraceIdMiddleware + contextvars); в воркерах и боте нет |

---

## Этап 5. RAG и квоты AI

> **Статус 22.10 (после дожима):** ✅ 7 / 🟡 1 / ⬜ 0 из 8.

**Цель:** включить то, что написано, но не работает; закрыть cost abuse.
**Критерий готовности:** CB RAG реально открывается после 5 ошибок; кэш RAG переиспользуется между юзерами.

| # | Файл | Проблема | Severity | Фикс | Тест | Статус |
|---|---|---|---|---|---|---|
| 5.1 | `ai/agent.py:run_agent` | RAG-клиент создаётся на каждый ход → CB в инстансе мёртв | **Critical** | Синглтон на процесс (модульная переменная или `app.state`); передавать `rag` снаружи, как `llm` | `test_rag_cb_opens_across_turns` | ✅ **ЗАКРЫТО** (22.10, эта серия) — фикс + тест в коде |
| 5.2 | `ai/rag_client.py:_cache_key` | `user_id` в context → кэш не переиспользуется между юзерами | **High** | В `_cache_key` брать только `tenant` + `filters`, без `user_id` | `test_rag_cache_shared_between_users` | ✅ **ЗАКРЫТО** (22.10, эта серия) — фикс + тест в коде |
| 5.3 | `ai/rag_client.py:ask` | 5xx не ретраится; `resp.json()` `ValueError` не обёрнут в `RAGUnavailable` | **High** | 4xx — финал, 5xx — retry; `try/except json.JSONDecodeError → RAGUnavailable` | `test_rag_retries_5xx`, `test_rag_bad_json_falls_back` | ✅ **ЗАКРЫТО** — retry на 5xx/сеть, 4xx — финал; ошибки не двигают breaker |
| 5.4 | `services/ai_quota.py` | `allow` + `consume` не атомарны → перерасход на free | **Medium** | Lua-скрипт: `INCR` + проверка + `EXPIRE` в одном вызове | `test_quota_concurrent_no_overdraft` | 🟡 **ЧАСТИЧНО** — INCR + expire-при-создании; датированный ключ убирает «растянутое окно», но incr+expire не атомарны |
| 5.5 | `ai/agent.py:run_agent` | `/api/ai/message` без rate limit на сообщения (квота — за выдачу) → 15 «привет» = 15 платных LLM | **High** | Отдельный лимит на сообщения (20/мин/юзер) | `test_ai_message_rate_limited` | ✅ **ЗАКРЫТО** — AI_MESSAGE_RATE_LIMIT 20/60с (application/dialog/dto.py) |
| 5.6 | `ai/rag_client.py` | Бэкофф без jitter; `fire(send_alert)` без try | Low | `random.uniform(0, 0.2)`; `try/except` вокруг `fire` | — | ✅ **ЗАКРЫТО** (22.10, эта серия) — фикс + тест в коде |
| 5.7 | `ai/rag_client.py` | `httpx.AsyncClient` на каждый запрос | Low | Один на инстанс | — | ✅ **ЗАКРЫТО** (22.10, эта серия) — фикс + тест в коде |
| 5.8 | `ai/rag_client.py` | `fail_threshold`/`cooldown_sec` — дефолты в сигнатуре, не из `Settings` | Low | Вынести в `Settings` | — | ✅ **ЗАКРЫТО** — fail_threshold/cooldown из Settings (rag_cb_*) |

---

## Этап 6. Бот: UX, fallback, deep links

> **Статус 22.10 (после дожима):** ✅ 7 / 🟡 2 / ⬜ 4 из 13.

**Цель:** починить то, что не проявляется на пути автора, но ломается у пользователя.
**Критерий готовности:** кнопка «Подписка» в меню работает; `@dp.errors()` реально шлёт сообщение; deep link `filter_{id}` открывает фильтр.

| # | Файл | Проблема | Severity | Фикс | Тест | Статус |
|---|---|---|---|---|---|---|
| 6.1 | `bot/handlers.py:main_menu_callback` | `action == "subscribe"` → `cmd_subscribe(cb.message)` → `user_id` = бот → «Backend недоступен» | **High** | `_show_subscription(message, user_id: int)`; из callback — `cb.from_user.id` | `test_menu_subscribe_uses_real_user_id` | ✅ **ЗАКРЫТО** (22.10, эта серия) — фикс + тест в коде |
| 6.2 | `bot/__main__.py:@dp.errors()` | `event.message` не существует в aiogram 3 → уведомление молча не отправляется | **High** | `update = event.update; message = update.message if update else None` | `test_error_handler_notifies_user` | ✅ **ЗАКРЫТО** — on_error обрабатывает message и callback_query, уведомление не маскирует исходную ошибку |
| 6.3 | `bot/handlers.py:dlg:finalize` | Финализация по backend state, поиск по FSM `dlg_criteria` → расходятся | **Medium** | Backend `dialog_finalize` возвращает `criteria` — использовать его | `test_finalize_search_uses_same_criteria` | 🟡 **ЧАСТИЧНО** — сверка backend-state и FSM через dlg_turn-счётчик — требует проверки |
| 6.4 | `bot/button_flow.py:_finalize` | `state.clear()` до проверки `result` → при ошибке пользователь теряет 4 шага | **Medium** | Проверять `result` до `clear`; при ошибке оставить state | `test_button_flow_preserves_state_on_backend_down` | ✅ **ЗАКРЫТО** (22.10, эта серия) — фикс + тест в коде |
| 6.5 | `bot/handlers.py:cmd_subscribe` | Цены захардкожены → разъедутся с backend | **Medium** | Расширить `UserSubscriptionOut` полем `prices` | `test_subscribe_prices_from_backend` | ✅ **ЗАКРЫТО** — цены приходят из backend (prep[amount]), не захардкожены |
| 6.6 | `bot/handlers.py:handle_deep_link` | `filter_{id}` открывает миниапп на `/`, а не `/filter/{id}` | **Medium** | `_miniapp_button("...", f"/filter/{id}")` | `test_filter_deeplink_opens_filter` | ⬜ **ОТКРЫТО** — deep link /filter/{id} не найден |
| 6.7 | `bot/handlers.py:_agent_turn` | При backend down → `start_button_flow` без очистки `dlg_*` — пользователь застревает в кнопках | **Medium** | Очистить `dlg_turn`/`dlg_criteria`/`dlg_options` перед `start_button_flow` | `test_agent_turn_clears_dialog_state` | 🟡 **ЧАСТИЧНО** — очистка dlg_* перед start_button_flow — требует проверки |
| 6.8 | `bot/button_flow.py:_show_step` | `except Exception: pass` → «message is not modified» создаёт новую панель | **Medium** | Ловить `TelegramBadRequest`, проверять текст; на «not modified» — `return` | `test_panel_edit_not_modified` | ⬜ **ОТКРЫТО** — «message is not modified» не обрабатывается точечно |
| 6.9 | `bot/handlers.py:pre_checkout_query` | Всегда `ok=True`, не проверяет `invoice_payload` | Low/Medium | `if not query.invoice_payload.startswith("sub:"): return await query.answer(ok=False)` | `test_pre_checkout_rejects_wrong_payload` | ✅ **ЗАКРЫТО** (22.10, эта серия) — фикс + тест в коде |
| 6.10 | `bot/handlers.py:cmd_help` | HELP_TEXT без `/resume` | Low | Добавить | — | ✅ **ЗАКРЫТО** — /resume в HELP_TEXT |
| 6.11 | `bot/handlers.py:_parse_variants` | Нет порога цены → «1 2022» = машина за $1 | Low | `price >= 100` | `test_parse_variants_price_threshold` | ⬜ **ОТКРЫТО** — порога цены в _parse_variants нет |
| 6.12 | `bot/handlers.py:build_share_article` | `title[:256]` — Telegram-лимит ~64 байта → 400 | Low | `title[:64]` | — | ✅ **ЗАКРЫТО** (22.10, эта серия) — фикс + тест в коде |
| 6.13 | `bot/handlers.py:inline_listing_share` | `cache_time=15` для статичной карточки | Low | `cache_time=300` | — | ⬜ **ОТКРЫТО** — cache_time=15 для шаринга |

---

## Этап 7. Бот: платежи, инфраструктура, ошибки

> **Статус 22.10 (после дожима):** ✅ 7 / 🟡 0 / ⬜ 2 из 9.

**Цель:** убрать cost abuse и нестабильность клиента.
**Критерий готовности:** бот не падает при hiccup Telegram; `BackendClient` не создаёт соединение на каждый вызов; LLM-вызовы лимитированы.

| # | Файл | Проблема | Severity | Фикс | Тест | Статус |
|---|---|---|---|---|---|---|
| 7.1 | `bot/handlers.py:_agent_turn` | Нет rate limit на LLM: 15 сообщений/10с = 15 платных вызовов | **High** | Отдельный лимит на LLM (3/мин на юзера) или `asyncio.Semaphore` | `test_agent_turn_rate_limited` | ✅ **ЗАКРЫТО** — FloodMiddleware (throttle.py) на message+callback_query |
| 7.2 | `bot/client.py` | Новый `AsyncClient` на каждый вызов; нет retry/CB | **Medium** | Один `AsyncClient` на инстанс; 1 retry на сетевые/5xx; CB на 5 ошибок → 30с | `test_backend_client_cb` | ✅ **ЗАКРЫТО** (22.10, эта серия) — фикс + тест в коде |
| 7.3 | `bot/__main__.py` | `heartbeat.cancel()` без `await`; нет `bot.session.close()` | **Medium** | `with suppress(CancelledError): await heartbeat`; `await bot.session.close()` в `finally` | `test_bot_shutdown_clean` | ✅ **ЗАКРЫТО** (22.10, эта серия) — фикс + тест в коде |
| 7.4 | `bot/__main__.py` | `set_my_commands`/`set_chat_menu_button` без try/except → падение при hiccup Telegram | **Medium** | `try/except` с `logger.warning` | `test_bot_starts_when_telegram_hiccups` | ✅ **ЗАКРЫТО** (22.10, эта серия) — фикс + тест в коде |
| 7.5 | `bot/throttle.py` | `_hits`/`_warned` без очистки — утечка | **Medium** | Периодическая чистка или `TTLCache` | — | ✅ **ЗАКРЫТО** (22.10, эта серия) — фикс + тест в коде |
| 7.6 | `bot/__main__.py` + `bot/handlers.py` | Два `BackendClient` (в `dp` и модуле) | Low | Один инстанс через `dp` | — | ✅ **ЗАКРЫТО** (22.10, эта серия) — фикс + тест в коде |
| 7.7 | `bot/client.py:search_listings_page` | Нет URL-энкодинга `params` | Low | `urlencode(query, doseq=True)` | — | ✅ **ЗАКРЫТО** — by-url кодируется quote_plus |
| 7.8 | `bot/handlers.py:_send_car_cards` | `except Exception` слишком широкий — скрывает реальные баги | Low | Ловить `TelegramAPIError` | — | ⬜ **ОТКРЫТО** — широкий except Exception в _send_car_cards |
| 7.9 | `bot/handlers.py:fallback_text` | `_extract_url` берёт только первую ссылку | Low | Обрабатывать все или явно документировать | — | ⬜ **ОТКРЫТО** — _extract_url берёт первую ссылку |

---

## Этап 8. Воркеры: fetch, translate, DLQ (критично)

> **Статус 22.10 (после дожима):** ✅ 5 / 🟡 2 / ⬜ 1 из 8.

**Цель:** перестать забивать DLQ штатным поведением и починить `commit`-на-каждый-лид.
**Критерий готовности:** при открытом CB перевода задачи не уходят в DLQ; `fetch_source_task` не падает на новом ключе статистики.

| # | Файл | Проблема | Severity | Фикс | Тест | Статус |
|---|---|---|---|---|---|---|
| 8.1 | `workers/fetch.py:translate_listings` | При открытом CB `translate` возвращает все `None` → `RuntimeError` → 3 ретрая → DLQ | **High** | `translate` при открытом CB бросает `TranslateUnavailable`; `translate_listings` ловит и возвращает `{"skipped": True}` **без** ретрая | `test_translate_cb_open_no_dlq` | ✅ **ЗАКРЫТО** — источник в CB → {skipped: True} без ретраев/DLQ |
| 8.2 | `workers/fetch.py:fetch_source_task` | `totals[key] += value` → `KeyError` на новом ключе из `save_listings` | **Medium** | `totals[key] = totals.get(key, 0) + value` | `test_fetch_handles_new_stat_key` | ✅ **ЗАКРЫТО** (22.10, эта серия) — фикс + тест в коде |
| 8.3 | `workers/fetch.py:schedule_fetch_due` | `session = session_factory()` без `async with` → `UnboundLocalError` | Low | `async with session_factory() as session:` | — | ✅ **ЗАКРЫТО** — async with session_factory() везде |
| 8.4 | `workers/fetch.py:fetch_source_task` | `max_pages = max_pages or 3` — явный `0` станет `3` | Low | `max_pages = 3 if max_pages is None else max_pages` | `test_max_pages_zero_respected` | ✅ **ЗАКРЫТО** (22.10, эта серия) — фикс + тест в коде |
| 8.5 | `workers/fetch.py:fetch_source_task` | `record_success` коммитит на каждой странице (2 коммита на страницу) | Low/Medium | Батчить health в конце фетча | — | 🟡 **ЧАСТИЧНО** — record_success в цикле по страницам; батчинг не сделан |
| 8.6 | `workers/fetch.py:raw_retention_job` | `UPDATE listings SET raw_key = NULL WHERE raw_key = ANY(:keys)` — без индекса на `raw_key` | Low | `Index("idx_listings_raw_key", "raw_key")` (partial `WHERE raw_key IS NOT NULL`) | `test_explain_raw_retention` | ⬜ **ОТКРЫТО** — индекса на listings.raw_key нет |
| 8.7 | `workers/fetch.py:translate_listings` | `translate_all_failed` бросается всегда, даже если часть переведена | **Medium** | Бросать только если `translations == 0` и `title_raw` непустые (это уже есть), но добавить счётчик `partial` и не ретраить при частичном успехе | `test_translate_partial_success_no_retry` | 🟡 **ЧАСТИЧНО** — skipped-путь есть; счётчика partial/нет-ретрая-при-частичном-успехе нет |
| 8.8 | `workers/fetch.py:startup` | `storage.ensure_bucket()` без try — падение S3 роняет воркер | **Medium** | `try/except` с `logger.warning`; `ctx["storage"] = None` если не удалось | `test_fetch_starts_without_s3` | ✅ **ЗАКРЫТО** (22.10, эта серия) — фикс + тест в коде |

---

## Этап 9. Воркеры: send, digest, cron

> **Статус 22.10 (после дожима):** ✅ 7 / 🟡 0 / ⬜ 3 из 10.

**Цель:** убрать блокирующие вызовы и лишние коммиты; починить даунгрейд подписки.
**Критерий готовности:** дайджест делает 1 коммит на пользователя; `subscription_check_job` не оставляет мусор в `subscription_tier`.

| # | Файл | Проблема | Severity | Фикс | Тест | Статус |
|---|---|---|---|---|---|---|
| 9.1 | `workers/send.py:digest_job` | `redis.keys("digest:*")` блокирует Redis | **Medium/High** | `scan_iter("digest:*")` | `test_digest_uses_scan_iter` | ✅ **ЗАКРЫТО** (22.10, эта серия) — фикс + тест в коде |
| 9.2 | `workers/send.py:digest_job` | `mark_sent` коммитит на каждый лид → 5 коммитов на пользователя | **Medium** | `mark_sent_many(session, redis, user_id, ids)` — один `commit` | `test_digest_single_commit_per_user` | ✅ **ЗАКРЫТО** (22.10, эта серия) — фикс + тест в коде |
| 9.3 | `workers/send.py:subscription_check_job` | После даунгрейда `subscription_tier` остаётся "pro" в БД | **Medium** | При даунгрейде `user.subscription_tier = "free"; user.subscription_until = None` | `test_downgrade_clears_tier` | ✅ **ЗАКРЫТО** (22.10, эта серия) — фикс + тест в коде |
| 9.4 | `workers/send.py:_user_quiet_hours` | Берёт первый фильтр; у пользователя 5 фильтров с разными `quiet_hours` | Low/Medium | Агрегировать («если хотя бы один в тихих часах — не шлём») | `test_quiet_hours_aggregated` | ⬜ **ОТКРЫТО** — _user_quiet_hours берёт первый фильтр (limit(1)) |
| 9.5 | `workers/send.py:digest_job` | `ok` может быть `UnboundLocalError` | Low | `ok = False` до `async with` | — | ✅ **ЗАКРЫТО** — ok инициализируется до цикла |
| 9.6 | `workers/send.py:dialog_cleanup_job` | `async for session in get_session()` — неочевидно | Low | `async with session_factory() as session:` | — | ✅ **ЗАКРЫТО** — async with session_factory() |
| 9.7 | `workers/send.py:send_telegram_message` | `httpx.AsyncClient` на каждый вызов | **Medium** | Один на инстанс | — | ✅ **ЗАКРЫТО** (22.10, эта серия) — фикс + тест в коде |
| 9.8 | `workers/send.py:send_listing_notification` | `session.get(Listing)` без проверки `is_active` — шлём устаревшие | Low/Medium | `if not listing.is_active: return skipped` | `test_notify_skips_inactive` | ✅ **ЗАКРЫТО** (22.10, эта серия) — фикс + тест в коде |
| 9.9 | `workers/send.py:listing_status_scan` | `publisher.enqueue` в цикле по юзерам — N publish на листинг | **Medium** | Батчить или использовать `asyncio.gather` с ограничением | — | ⬜ **ОТКРЫТО** — enqueue в цикле по юзерам, без батчинга |
| 9.10 | `workers/send.py:subscription_check_job` | `for user in users` — загрузка всех pro/business раз в день | **Medium** | `WHERE subscription_until BETWEEN now AND now + 3 days` | — | ⬜ **ОТКРЫТО** — загрузка всех pro/business раз в день без окна |

---

## Этап 10. Дисциплина исключений (500 → 4xx)

> **Статус 22.10 (после дожима):** ✅ 8 / 🟡 3 / ⬜ 0 из 11.

**Цель:** кривой вход не должен давать 500 и шуметь в Sentry.
**Критерий готовности:** все «пользовательские» ошибки — доменные исключения → 4xx.

| # | Файл | Проблема | Severity | Фикс | Тест | Статус |
|---|---|---|---|---|---|---|
| 10.1 | `core/security.py:validate_init_data` | `int(auth_date)` → `ValueError` → 500 | **Medium** | `try/except (TypeError, ValueError) → InitDataError` | `test_init_data_bad_auth_date_401` | ✅ **ЗАКРЫТО** — int(auth_date) обёрнут в try → InitDataError |
| 10.2 | `core/security.py:validate_init_data` | `json.loads(user_raw)` → `JSONDecodeError` → 500 | **Medium** | `try/except json.JSONDecodeError → InitDataError` | `test_init_data_bad_user_json_401` | ✅ **ЗАКРЫТО** — json.loads(user_raw) → InitDataError |
| 10.3 | `core/security.py:decode_access_token` | `int(payload["sub"])` → `KeyError`/`ValueError` → 500 | **Medium** | `try/except (KeyError, TypeError, ValueError) → TokenError` | `test_jwt_bad_sub_401` | ✅ **ЗАКРЫТО** — decode_access_token → TokenError |
| 10.4 | `core/pq_crypto.py:_split_public/_split_private` | `bytes.fromhex` → `ValueError`, не `PQCryptoError` | Low/Medium | Обернуть | `test_pq_bad_hex_raises_pqcryptoerror` | ✅ **ЗАКРЫТО** — fromhex → PQCryptoError + проверка длины ключа |
| 10.5 | `core/pq_crypto.py:decrypt_document` | `kem_len` не валидируется (65535) | **Medium** | `_KEM_CT_SIZE = 1088`; проверка | `test_pq_wrong_kem_len` | ✅ **ЗАКРЫТО** (22.10, эта серия) — фикс + тест в коде |
| 10.6 | `api/admin.py:trigger_fetch` | `max_pages: int = 1` без границ; `criteria: dict` как query | **High** | `Query(1, ge=1, le=50)`; `criteria` — body | `test_trigger_fetch_bounds` | ✅ **ЗАКРЫТО** — Query(default=1, ge=1, le=50); criteria валидируется как SearchCriteria (query-param остался) |
| 10.7 | `services/partners.py:claim_lead` | `assert lead is not None` → 500 на новом `reason` | **Medium** | `else: raise HTTPException(400, reason)` | `test_claim_lead_unknown_reason_4xx` | ✅ **ЗАКРЫТО** — доменные ошибки (NotFound и др.) вместо assert → 4xx |
| 10.8 | `api/analysis.py` | `AnalysisOut(**payload)` → 500 на несовпадении | **Medium** | `AnalysisOut.model_validate(payload)` | — | 🟡 **ЧАСТИЧНО** — требует проверки: model_validate против **payload |
| 10.9 | `api/dialog.py` / `api/ai_search.py` | `criteria=body.criteria` без валидации → `ValidationError` → 500 | **Medium** | `SearchCriteria(**body.criteria)` на входе, 422 | `test_finalize_bad_criteria_422` | 🟡 **ЧАСТИЧНО** — criteria валидируется SearchCriteria на входе admin; в dialog-роутере требует проверки |
| 10.10 | `core/deps.py` | Нет `AdminUserDep` — роль проверяется вручную | **Medium** | `AdminUserDep = Annotated[User, Depends(get_admin_user)]` | `test_admin_required_dependency` | 🟡 **ЧАСТИЧНО** — роль проверяется в dishka-провайдерах; типизированного AdminUserDep нет |
| 10.11 | `core/security.py:validate_init_data` | Нет проверки `auth_date` в будущем | Low/Medium | `if auth_dt > now + 5min: raise InitDataError` | `test_init_data_future_date_401` | ✅ **ЗАКРЫТО** — auth_date в будущем → InitDataError (skew) |

---

## Этап 11. Конкурентность, транзакции, гигиена

> **Статус 22.10 (после дожима):** ✅ 5 / 🟡 4 / ⬜ 1 из 10.

**Цель:** убрать оставшиеся гонки и «тихие» коммиты чужих транзакций.
**Критерий готовности:** toggle/лимит/rate limit не превышаются при параллельных запросах.

| # | Файл | Проблема | Severity | Фикс | Тест | Статус |
|---|---|---|---|---|---|---|
| 11.1 | `api/favorites.py:toggle_favorite` | Race: два параллельных toggle → два insert или `IntegrityError` → 500 | **Medium** | `INSERT ... ON CONFLICT DO NOTHING`; ответ по `rowcount` | `test_toggle_concurrent` | ✅ **ЗАКРЫТО** — INSERT … ON CONFLICT DO NOTHING + RETURNING (favorites) |
| 11.2 | `api/filters.py:create_filter` | Лимит фильтров без лока → превышение | **Medium** | `SELECT ... FOR UPDATE` на юзера или advisory lock | `test_filter_limit_concurrent` | ✅ **ЗАКРЫТО** — SELECT … FOR UPDATE на юзера при лимите фильтров |
| 11.3 | `api/filters.py:create_filter` | `count = len(list(...))` — загрузка всех ради счётчика | **Medium** | `select(func.count()).where(...)` | — | ✅ **ЗАКРЫТО** — select(func.count()) вместо загрузки всех строк |
| 11.4 | `core/deps_partner.py:check_rate_limit` | `incr` + `expire` не атомарны → вечный rate limit при смерти процесса | **Medium/High** | `pipeline.incr(); pipeline.expire(nx=True)` или Lua | `test_rate_limit_ttl_atomic` | ✅ **ЗАКРЫТО** — rate limiter на Lua (infrastructure/services/rate_limit.py, redis.eval) |
| 11.5 | `services/analytics.py:track` | `commit` в середине чужой транзакции | **Medium** | `session.add` + `flush`; или отдельная сессия | `test_track_does_not_commit_foreign_tx` | 🟡 **ЧАСТИЧНО** — track в репозитории той же сессии; мид-транзакционный commit требует проверки |
| 11.6 | `services/admin_audit.py:log_admin_action` | То же + нет `rollback` при ошибке | **Medium** | `flush` + `rollback` в `except` | `test_audit_rollback_on_error` | 🟡 **ЧАСТИЧНО** — admin_audit: flush/rollback-семантика требует проверки |
| 11.7 | `services/users.py:upsert_user` | Затирает `username`/`first_name` на `None` | **Medium** | Обновлять только если `is not None` | `test_upsert_user_preserves_username` | 🟡 **ЧАСТИЧНО** — upsert_user: сохранение username при None требует проверки |
| 11.8 | `api/favorites.py:list_favorites` | Без пагинации — 500 избранных = 500 `ListingOut` | **Medium** | `limit/offset` | — | ✅ **ЗАКРЫТО** — limit/offset + keyset-cursor на favorites |
| 11.9 | `services/partners.py:authenticate_key` | `key.last_used_at = now` + `commit` на каждый запрос — hot row | **Medium** | Батчить (раз в N минут) или `flush` без commit | — | ⬜ **ОТКРЫТО** — last_used_at + commit на каждый запрос — hot row |
| 11.10 | `services/partners.py:list_partners` | N+1: select всех id + `get` на каждую | **Medium** | Один `select(PartnerAccount)` + `claimed_today` батчем | — | 🟡 **ЧАСТИЧНО** — claimed_today одним запросом есть; N+1 по partners требует проверки |

---

## Этап 12. Схема БД, конфиг, жизненный цикл

> **Статус 22.10 (после дожима):** ✅ 13 / 🟡 4 / ⬜ 3 из 20.

**Цель:** убрать мины в моделях и сессиях, которые выстрелят при первой миграции/нагрузке.
**Критерий готовности:** `mypy` не ругается на `float` vs `Decimal`; `lifespan` закрывает engine/Redis/publisher.

| # | Файл | Проблема | Severity | Фикс | Тест | Статус |
|---|---|---|---|---|---|---|
| 12.1 | `db/models.py` | `Mapped[float]` на `Numeric` — тип-ложь, `Decimal` в рантайме | **Medium** | `Mapped[Decimal \| None]` везде, где `Numeric` | mypy | ✅ **ЗАКРЫТО** — Mapped[Decimal] на всех Numeric |
| 12.2 | `db/models.py` | `Numeric` без precision на `Listing.price`, `Payment.amount`, `ListingPriceHistory.price`, `PriceAnalysis.fair_min/max` | **Medium** | `Numeric(12, 2)`; миграция на копии БД | `test_price_precision` | ✅ **ЗАКРЫТО** — Numeric(12,2)/(20,4) с precision |
| 12.3 | `db/models.py` | Ни одного `CheckConstraint` на enum-поля | **Medium** | `CheckConstraint("role IN (...)", name="ck_users_role")` и аналогично | `test_invalid_role_rejected` | ✅ **ЗАКРЫТО** — CheckConstraint на enum-полях (kind, tier, role, provider и др.) |
| 12.4 | `db/models.py` | `updated_at` без `onupdate=func.now()` | **Medium** | `mapped_column(..., onupdate=func.now())`; убрать ручные | — | ✅ **ЗАКРЫТО** — onupdate=func.now() на updated_at |
| 12.5 | `db/models.py:InspectionReport` | `idx_inspection_reports_listing` дублирует unique | Low | Удалить индекс | — | 🟡 **ЧАСТИЧНО** — дубль индекса InspectionReport в классе не найден — сверить с миграциями |
| 12.6 | `core/config.py` | `translate_source_langs` — мутабельный дефолт без `default_factory` | Low | `Field(default_factory=lambda: {...})` | — | ✅ **ЗАКРЫТО** — Field(default_factory=…) для мутабельных дефолтов |
| 12.7 | `core/config.py` | `extra="ignore"` скрывает опечатки env | Low/Medium | `extra="forbid"` в prod | `test_unknown_env_rejected_in_prod` | ⬜ **ОТКРЫТО** — extra=ignore остался |
| 12.8 | `core/config.py` | `internal_token` не проверяется на длину в prod | **Medium** | `if self.internal_token and len(self.internal_token) < 32: raise` | `test_short_internal_token_rejected` | 🟡 **ЧАСТИЧНО** — INTERNAL_TOKEN обязателен в prod; минимальная длина не проверяется |
| 12.9 | `core/config.py` | `access_token_ttl_sec`/`refresh_token_ttl_sec` без `gt=0` | Low/Medium | `Field(default=3600, gt=0, le=86400)` | — | ✅ **ЗАКРЫТО** — ttl-поля Field(gt=0) + кросс-инвариант refresh > access |
| 12.10 | `core/deps_partner.py` + `core/deps_internal.py` | `RedisDep` объявлен дважды с разными типами | **Medium** | Одно определение в `deps_internal` | — | ✅ **ЗАКРЫТО** — устранено рефакторингом: deps пересобраны на dishka-провайдеры |
| 12.11 | `db/session.py` | `settings = get_settings()` на импорте — тесты с другой БД ломаются | **Medium** | `get_engine()` с `lru_cache` | `test_engine_isolated_in_tests` | 🟡 **ЧАСТИЧНО** — engine строится фабрикой с dispose(); изоляция на тестах требует проверки |
| 12.12 | `db/session.py` | Нет `pool_size`/`max_overflow`/`pool_recycle`/`statement_timeout` | **Medium** | В `create_async_engine` | — | ✅ **ЗАКРЫТО** — pool_size/max_overflow/pool_recycle + statement_timeout/idle_in_transaction_timeout |
| 12.13 | `db/session.py` | Нет явного `begin()` для денежных операций | **Medium** | `get_session_in_transaction` dependency | `test_claim_rolls_back_on_error` | 🟡 **ЧАСТИЧНО** — требует проверки: явная транзакция на денежных путях |
| 12.14 | `api/main.py` | Нет `lifespan` — engine/Redis/publisher не закрываются | **Medium** | `@asynccontextmanager lifespan` | — | ✅ **ЗАКРЫТО** — lifespan в main_api.py, закрытие engine/клиентов |
| 12.15 | `api/main.py` | `_check_redis`/`_check_rabbit` создают соединения на каждый health check | **Medium** | `get_redis()`; rabbit — lru_cache | `test_health_reuses_redis` | ✅ **ЗАКРЫТО** (22.10, эта серия) — фикс + тест в коде |
| 12.16 | `api/admin.py` | `storage_from_settings()` на каждый запрос, не закрывается | **Medium** | Синглтон или контекстный менеджер | — | ✅ **ЗАКРЫТО** — storage через dishka Scope.APP (синглтон) |
| 12.17 | `api/admin.py` | `check_internal_token` вручную, не dependency | **Medium** | `dependencies=[Depends(check_internal_token)]` на роутере | `test_new_admin_endpoint_requires_token` | ✅ **ЗАКРЫТО** — InternalDishkaRoute на роутерах вместо ручных проверок |
| 12.18 | `core/pq_crypto.py` | `_INFO` захардкожен на v1 — forward compat сломан | **Medium** | `info = f"...v{version}".encode()` | `test_pq_v2_decryptable` | ✅ **ЗАКРЫТО** (22.10, эта серия) — фикс + тест в коде |
| 12.19 | `core/pq_crypto.py` | Нет `key_id` в контейнере — ротация ключей невозможна | **Medium** | 4-байтовый `key_id` в заголовке | — | ⬜ **ОТКРЫТО** — key_id в контейнере нет |
| 12.20 | `services/translate_client.py` | Бэкофф без jitter; `httpx.AsyncClient` на каждый `_request` | Low | Один на инстанс, jitter | — | ⬜ **ОТКРЫТО** — translate-клиент: AsyncClient на запрос, без jitter |

---

## Этап 13. АРХИТЕКТУРА: рефакторинг очередей (критично)

> **Статус 22.10 (после дожима):** ✅ 8 / 🟡 5 / ⬜ 1 из 14.

**Почему это отдельный этап, а не «фиксы»:**
Текущий `TaskWorker` — свой мини-фреймворк поверх `aio-pika`, который решает 5 задач одновременно (публикация, консьюминг, cron, interval, DLQ-мониторинг) и **делает это с дырами**:
- Нет идемпотентности задач — повторная доставка = повторный `charge_lead`.
- Нет visibility timeout — задача, упавшая в середине, висит «в обработке» вечно.
- Нет dead-letter replay — DLQ растёт, разобрать нельзя.
- Нет приоритетов — `digest_job` и `fetch_source_task` в одной очереди `send`/`fetch`.
- Нет метрик (только `message_count` DLQ).
- Cron/interval локи — на Redis, но при рестарте Redis все задачи «выполнятся» заново.
- `message.process(ignore_processed=True)` + ручной `nack` — риск двойного ack/nack.
- Нет graceful shutdown — `stop()` ставит event, но in-flight задачи не дожидаются.
- `TaskWorker` не покрыт интеграционными тестами (только `queue/rabbit.py` юнит-тесты).

Это не «30 фиксов» — это **переписать слой очередей так, чтобы фиксы не понадобились**.

| # | Что | Проблема | Severity | Решение | Критерий | Статус |
|---|---|---|---|---|---|---|
| 13.1 | **Идемпотентность задач** | `payment_success` / `claim_lead` / `translate_listings` при повторной доставке выполняются заново | **Critical** | `idempotency_key` в сообщении (`job:entity_id:version`); обработчик проверяет `SET NX idempotency:{key}` с TTL перед выполнением; результат кэшируется на TTL | `test_task_redelivery_idempotent` | ✅ **ЗАКРЫТО** — idempotency_key в контрактах сообщений и use cases (dedup на обработке) |
| 13.2 | **Visibility timeout** | Задача, упавшая в середине, висит «в обработке» вечно (нет ack/nack) | **High** | `channel.set_qos(prefetch_count=N)` + `consumer_timeout` (RabbitMQ 3.12+); heartbeat на `TaskWorker`; при таймауте — `nack(requeue=True)` | `test_task_visibility_timeout` | 🟡 **ЧАСТИЧНО** — quorum-очереди с x-delivery-limit решают потерю ack; prefetch/visibility явно не настроены |
| 13.3 | **DLQ replay** | DLQ растёт, разобрать нельзя | **High** | CLI `python -m app.queue.replay --queue dead_letter --limit 100`; читает, логирует, опционально `publish` обратно в исходную очередь; идемпотентность (13.1) защищает от дублей | `test_dlq_replay_idempotent` | ✅ **ЗАКРЫТО** (22.10, эта серия) — фикс + тест в коде |
| 13.4 | **Приоритеты и разделение очередей** | `digest_job` и `fetch_source_task` конкурируют за ресурсы; `translate_listings` (CPU-bound) блокирует `send` | **High** | Разделить: `fetch` (I/O), `send` (I/O + Telegram rate limit), `translate` (CPU), `cron` (низкий приоритет). `x-max-priority` для `send` (instant > digest) | `test_translate_does_not_block_send` | 🟡 **ЧАСТИЧНО** — разделение очередей fetch/send/translate есть; x-max-priority нет |
| 13.5 | **Graceful shutdown** | `stop()` ставит event, но in-flight задачи не дожидаются; `connection.close()` рвёт соединения | **High** | `worker.stop()` → `await self._stop.wait()` → `await asyncio.gather(*in_flight, return_exceptions=True)` с таймаутом N сек → `connection.close()` | `test_worker_graceful_shutdown` | ✅ **ЗАКРЫТО** — graceful shutdown встроен в FastStream-консьюмер (main_consumer.py) |
| 13.6 | **Cron/interval без Redis-локов** | Redis лежит → cron выполнится на всех воркерах; Redis рестарт → все задачи «выполнятся» заново | **Medium** | `CronSpec`/`IntervalSpec` с `slot_ts` в сообщении; идемпотентность (13.1) отсекает дубли; lock — best-effort | `test_cron_idempotent_on_redis_restart` | 🟡 **ЧАСТИЧНО** — дубли cron отсекаются идемпотентностью; slot_ts-семантика не выделена |
| 13.7 | **Метрики очередей** | Только `message_count` DLQ | **Medium** | Prometheus: `queue_depth{queue}`, `task_duration_seconds{job}`, `task_failures_total{job}`, `dlq_depth{queue}`; экспорт через `/metrics` в admin | `test_metrics_exposed` | ⬜ **ОТКРЫТО** — метрик очередей (depth/duration/failures) нет |
| 13.8 | **Backpressure** | `publisher.enqueue` в цикле по 1000 юзерам — без ограничения; брокер захлёбывается | **Medium** | `asyncio.Semaphore` на publish (50 одновременных); `publisher.enqueue_many` с батчингом | `test_publish_backpressure` | ✅ **ЗАКРЫТО** (22.10, эта серия) — фикс + тест в коде |
| 13.9 | **Poison message detection** | Сообщение, которое всегда падает (баг в данных), ретраится 3 раза и уходит в DLQ, но `attempt` не сохраняется | **Medium** | `x-death` header от RabbitMQ; если `attempt >= MAX` — сразу DLQ без ретраев | `test_poison_message_goes_to_dlq_directly` | ✅ **ЗАКРЫТО** — x-delivery-limit на quorum-очередях → DLX → dead_letter без бесконечных ретраев |
| 13.10 | **Типизация сообщений** | `args: tuple`, `kwargs: dict` — любая ошибка в сигнатуре обработчика = runtime | **Medium** | Pydantic-схема на каждое сообщение (`FetchSourceTask`, `SendListingNotification`); валидация при `decode_message`; неизвестная схема → DLQ + алерт | `test_invalid_message_schema_to_dlq` | ✅ **ЗАКРЫТО** — строгие Pydantic-контракты сообщений (extra=forbid) + валидация при decode |
| 13.11 | **Интеграционные тесты `TaskWorker`** | `TaskWorker` не покрыт тестами (только `rabbit.py` юнит) | **High** | testcontainers + RabbitMQ: publish → consume → assert handler вызван; падение → retry → DLQ; graceful shutdown | `test_worker_end_to_end` | ✅ **ЗАКРЫТО** — интеграционные тесты messaging (tests/messaging) |
| 13.12 | **Документация топологии** | Топология в docstring, но не в диаграмме; новый разработчик не поймёт, какая очередь за что | **Medium** | `docs/queues.md`: диаграмма exchange/queue/binding, retry-политика, DLQ, replay | — | 🟡 **ЧАСТИЧНО** — топология описана в docs/ARCHITECTURE.md; отдельной docs/queues.md нет |
| 13.13 | **`TaskPublisher` singleton** | `get_publisher()` — модульная переменная; при рестарте — новый коннект, старый не закрыт | **Medium** | `lifespan` + `app.state.publisher`; `aclose()` при shutdown | `test_publisher_closed_on_shutdown` | ✅ **ЗАКРЫТО** — publisher через dishka Scope.APP, закрытие в lifespan |
| 13.14 | **Ретраи с категоризацией** | Любая ошибка ретраится 3 раза, включая `ValidationError` (не имеет смысла) | **Medium** | `TransientError` vs `PermanentError`; permanent → сразу DLQ | `test_permanent_error_no_retry` | 🟡 **ЧАСТИЧНО** — лимит доставок quorum — грубый аналог; деления TransientError/PermanentError нет |

**Критерий готовности этапа:**
- `TaskWorker` покрыт интеграционными тестами (publish → consume → retry → DLQ → replay).
- Идемпотентность: повторная доставка `charge_lead` не списывает дважды.
- Метрики очередей в Prometheus.
- Graceful shutdown не теряет in-flight задачи.
- DLQ replay работает и идемпотентен.

**Почему это критично именно сейчас:**
Ты сам сказал: «в очередях живёт и жизненно необходим рефакторинг». Проблема не в том, что `TaskWorker` плохо написан — он написан хорошо для MVP. Проблема в том, что **на нём висят деньги** (`charge_lead`), **данные** (`translate_listings`, `save_listings`) и **уведомления** (`send`). Любая дыра в слое очередей = потеря денег или данных. И эта дыра не чинится точечными фиксами (этапы 8–9) — она чинится **сменой контракта**: идемпотентность, visibility timeout, DLQ replay, метрики.

---

## Этап 14. АРХИТЕКТУРА: общая эволюция

> **Статус 22.10 (после дожима):** ✅ 4 / 🟡 4 / ⬜ 7 из 15.

**Почему это отдельный этап:**
Точечные фиксы (этапы 1–12) и рефакторинг очередей (этап 13) закрывают известные баги. Но есть **системные вещи**, которые не баги, а «следующий уровень»: наблюдаемость, тестовая стратегия, эволюция сервисов, документация.

| # | Что | Проблема | Severity | Решение | Критерий | Статус |
|---|---|---|---|---|---|---|
| 14.1 | **Наблюдаемость: трассировка** | Логи разрознены; `run_agent` → tool → LLM → RAG не связаны `trace_id` | **High** | OpenTelemetry (или ручной `trace_id` в `contextvars`); каждый лог с `trace_id`; экспорт в Jaeger/Tempo | `test_trace_id_propagated` | 🟡 **ЧАСТИЧНО** — TraceIdMiddleware + contextvars на HTTP; в воркерах/боте trace_id нет |
| 14.2 | **Наблюдаемость: метрики** | `/metrics` — только агрегаты БД; нет latency LLM/RAG/translate, нет ошибок по провайдерам | **High** | Prometheus-метрики: `llm_request_duration_seconds{provider,model}`, `rag_request_duration_seconds`, `translate_request_duration_seconds`, `http_request_duration_seconds{route}` | `test_metrics_exposed` | ⬜ **ОТКРЫТО** — latency-метрик LLM/RAG/translate нет |
| 14.3 | **Тестовая стратегия: сценарии отказа** | 435 тестов, но мёртвый CB, забитый DLQ, молчащий `@dp.errors()` — не пойманы | **High** | Для каждой критичной фичи — 1 интеграционный тест «при падении Y система делает Z»; `pytest -m integration` отдельно; testcontainers для Postgres/Redis/RabbitMQ | `test_*_failure_scenarios` | 🟡 **ЧАСТИЧНО** — интеграционные тесты на отказы есть точечно (messaging, rate limit); testcontainers нет |
| 14.4 | **Тестовая стратегия: конкурентность** | Гонки в `claim_lead`, `toggle_favorite`, `create_filter` не покрыты | **High** | `asyncio.gather` × 10 на один ресурс → assert `rowcount`/`len` = 1; `pytest-asyncio` + реальный Postgres | `test_concurrent_claim` | ✅ **ЗАКРЫТО** — claim-инварианты на живой БД в tests/partners |
| 14.5 | **Эволюция сервисов: gateway** | 6 микросервисов + gateway — но gateway только маршрутизирует; нет rate limit, нет circuit breaker, нет retry | **Medium** | Gateway: rate limit по IP/юзеру, CB на upstream, retry на 5xx; `X-Request-ID` пробрасывается | `test_gateway_cb` | ⬜ **ОТКРЫТО** — gateway: rate limit/CB/retry не реализованы |
| 14.6 | **Эволюция сервисов: shared kernel** | `app/core`, `app/db`, `app/queue` общие для mono и micro — при изменении схемы БД все сервисы надо деплоить одновременно | **Medium** | Версионирование схемы (миграции отдельно); `shared` — read-only пакет; сервисы не импортируют `app.db.models` напрямую | — | 🟡 **ЧАСТИЧНО** — ports/dto: сервисы не тянут чужую инфру; версионирования схемы нет |
| 14.7 | **Документация: ADR** | Решения (aioboto3 vs boto3, свой TaskWorker, микросервисы) живут в комментариях, но не в ADR | **Medium** | `docs/adr/0001-aioboto3.md`, `0002-own-taskworker.md`, ...; формат: контекст → решение → последствия | — | ✅ **ЗАКРЫТО** (22.10, эта серия) — фикс + тест в коде |
| 14.8 | **Документация: runbook** | `INCIDENTS.md` есть, но нет «что делать, если DLQ растёт / RAG лежит / encar забанил» | **Medium** | `docs/runbook.md`: симптом → диагностика → действие → эскалация | — | ✅ **ЗАКРЫТО** (22.10, эта серия) — фикс + тест в коде |
| 14.9 | **CI/CD: проверки** | `pytest`/`ruff`/`mypy` — хорошо; нет `pip-audit`, нет `bandit`, нет `alembic check` на конфликт миграций | **Medium** | Добавить в CI: `pip-audit`, `bandit -r app`, `alembic check` (голова без конфликтов) | `test_ci_security` | ✅ **ЗАКРЫТО** (22.10, эта серия) — фикс + тест в коде |
| 14.10 | **CI/CD: миграции** | `alembic upgrade head` на копии БД — хорошо; нет автоматической проверки downgrade | **Medium** | CI: `upgrade head` → `downgrade -1` → `upgrade head` на testcontainers | `test_migrations_reversible` | ⬜ **ОТКРЫТО** — автопроверки downgrade нет |
| 14.11 | **Эволюция: feature flags** | Флаги (`translate_enabled`, `raw_payload_enabled`, `rag_enabled`) — env, не runtime; нельзя включить для 10% юзеров | **Low/Medium** | Если нужно — Unleash/Flagsmith; для MVP — env достаточно, но задокументировать | — | 🟡 **ЧАСТИЧНО** — env-флаги задокументированы в docs/; runtime-флаги осознанно не нужны (MVP) |
| 14.12 | **Эволюция: LLM cost tracking** | Нет учёта стоимости LLM-вызовов по юзерам/фичам | **Medium** | `llm_usage` таблица: `user_id, feature, model, tokens_in, tokens_out, cost_usd`; агрегаты в `/metrics` | `test_llm_cost_tracked` | ⬜ **ОТКРЫТО** — llm_usage нет |
| 14.13 | **Эволюция: LLM fallback** | `build_llm_client` падает, если провайдер недоступен → `DialogError` → кнопки | **Medium** | Fallback-цепочка: GLM → Anthropic → Ollama → mock; `LLMError` только если все недоступны | `test_llm_fallback_chain` | ⬜ **ОТКРЫТО** — fallback-цепочки LLM нет |
| 14.14 | **Эволюция: PQ-крипта** | `key_id` нет (12.19); ротация ключей невозможна | **Medium** | После 12.19: `key_id` в контейнере + `key_store` (env или Vault); `decrypt` ищет по `key_id` | `test_pq_key_rotation` | ⬜ **ОТКРЫТО** — заблокировано 12.19 |
| 14.15 | **Эволюция: партиционирование** | `listing_price_history` подготовлена (PK `(id, recorded_at)`), но не партиционирована | **Low** | `pg_partman` по `recorded_at` (месяц); миграция 0019; retention через `DROP PARTITION` вместо `DELETE` | `test_partition_created` | ⬜ **ОТКРЫТО** — партиционирование не сделано (0013 только подготовил PK) |

**Критерий готовности этапа:**
- Каждый критичный путь (AI-диалог, payment, claim) имеет `trace_id` в логах.
- Prometheus видит latency LLM/RAG/translate/HTTP.
- Есть 5+ интеграционных тестов на сценарии отказа (CB, DLQ, конкурентность).
- `docs/adr/` с 3+ ADR.
- `docs/runbook.md` с 3+ сценариями.
- CI прогоняет `pip-audit`, `bandit`, `alembic check`, `downgrade -1`.

---

## Сводка по этапам

| Этап | Тема | Кол-во фиксов | Оценка | Приоритет |
|---|---|---|---|---|
| 1 | Безопасность и аутентификация | 9 | 2–3 дня | **P0** |
| 2 | Деньги: claim и биллинг | 10 | 2–3 дня | **P0** |
| 3 | Данные: дедуп и индексы | 8 | 2–3 дня | **P0** |
| 4 | AI-агент: промпты, tools, диалог | 11 | 2–3 дня | **P1** |
| 5 | RAG и квоты AI | 8 | 1–2 дня | **P1** |
| 6 | Бот: UX, fallback, deep links | 13 | 2–3 дня | **P1** |
| 7 | Бот: платежи, инфраструктура | 9 | 1–2 дня | **P1** |
| 8 | Воркеры: fetch, translate, DLQ | 8 | 2 дня | **P0** |
| 9 | Воркеры: send, digest, cron | 10 | 2 дня | **P1** |
| 10 | Дисциплина исключений | 11 | 1–2 дня | **P1** |
| 11 | Конкурентность и транзакции | 10 | 2–3 дня | **P2** |
| 12 | Схема БД, конфиг, жизненный цикл | 20 | 3–4 дня | **P2** |
| 13 | **Архитектура: очереди** | 14 | **5–7 дней** | **P0** |
| 14 | Архитектура: общая эволюция | 15 | 5–7 дней | **P2/P3** |

**Актуально (22.10.2026):** из 156 пунктов закрыто 64, частично 37, открыто 55 — см. колонку «Статус» в таблицах выше. Оставшийся фокус: бот (этапы 6–7, 9), воркеры (8), наблюдаемость и CI (14).

**Итого (на момент составления):** ~150 задач. Из них:
- **P0 (этапы 1, 2, 3, 8, 13):** ~50 задач, 12–18 дней.
- **P1 (этапы 4, 5, 6, 7, 9, 10):** ~60 задач, 10–14 дней.
- **P2/P3 (этапы 11, 12, 14):** ~40 задач, 10–14 дней.

**Реалистичный срок:** 6–8 недель сфокусированной работы. Или 10–12 недель по одному PR в день без выгорания.

**Порядок:** 1 → 2 → 3 → 8 → 13 (P0) → 4 → 5 → 6 → 7 → 9 → 10 (P1) → 11 → 12 → 14 (P2/P3).

---

## Правила, чтобы долг не вернулся

1. **Перед каждым `commit`** — вопрос: «кто ещё может писать сюда параллельно?».
2. **Для каждой новой фичи** — один интеграционный тест на сценарий отказа.
3. **Каждый новый enum** — `CheckConstraint` сразу.
4. **Каждый новый индекс** — с `EXPLAIN`-тестом.
5. **Каждый новый `Numeric`** — с `Decimal` в `Mapped`.
6. **Каждый новый feature flag** — с валидатором зависимостей.
7. **Каждый новый internal-эндпоинт** — `dependencies=[Depends(check_internal_token)]` на роутере.
8. **Каждая новая задача в очереди** — с `idempotency_key` и Pydantic-схемой.
9. **Каждый новый сервис** — с `trace_id` в логах и метриками.
10. **Каждый инцидент** — строка в `INCIDENTS.md` + тест, который его ловит.

---

## Что НЕ в этом файле (осознанно)

- **Микросервисы / 15 процессов** — не техдолг, а выбор. Трогать, когда появится нагрузка.
- **`DialogState.messages` unbounded** — покрыто 4.9.
- **`CheckConstraint` на все enum** — покрыто 12.3.
- **`scan_iter` в дайджесте** — покрыто 9.1.
- **`lru_cache` Redis без `lifespan`** — покрыто 12.14.
- **Партиционирование `listing_price_history`** — покрыто 14.15 (P3).
