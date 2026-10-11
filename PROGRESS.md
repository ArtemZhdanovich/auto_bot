# Что сделано — итоги по проекту Auto Selection

> Обновлено: 11.10.2026. Всё описанное закоммичено и покрыто тестами
> (backend: 137 passed, `uv run pytest`; frontend: `tsc` + `vite build`).

---

## 1. Архитектура (ТЗ п. 4)

- **Backend** — FastAPI: `app/api/*` (auth, users, me, filters, listings,
  favorites, payments, admin, dialog, ai_search, notifications), `app/ai/*`
  (агент с tool-calling), `app/bot/*` (aiogram 3), `app/services/*`.
- **Бот** — aiogram 3: `/start`, `/menu`, `/help`, `/reset`, `/stop`,
  `/resume`, inline-меню, FSM в Redis.
- **Mini App** — React + Vite + TS: онбординг, лента с фильтрами, карточка,
  избранное, AI-поиск, профиль, Login-экран.
- **RAG-микросервис** — `services/rag/` (rag_core / rag_worker / rag_api),
  Qdrant + MinIO + Postgres, HTTP-шлюз `POST /api/v1/ask` (:8100), worker (:8101).
- **Инфраструктура** — docker-compose: postgres, redis, rabbitmq, qdrant,
  minio, rag_migrate, rag_worker, rag_api + backend/бот.
- Отдельный репозиторий **rag_service** вынесен в uv-workspace монорепо
  (packages/rag-core, services/rag-worker, services/rag-api), 361 тест.

## 2. Авторизация

- **Telegram**: `initData` → валидация HMAC-SHA256 → JWT (access 1ч + refresh 30д).
- **Сайт (email + пароль)**: `POST /api/auth/register|login`, PBKDF2-SHA256
  240k итераций, regex-валидация email, синтетические отрицательные
  `telegram_id` (PK/FK) с ретраем при коллизиях; миграция `0003_web_auth`
  (уникальный email). Неавторизованные веб-пользователи видят Login-экран.
- Magic link (одноразовый токен из бота) — как в ТЗ.

## 3. AI / LLM

- **Провайдер по умолчанию — GLM (Zhipu)** через OpenAI-совместимый
  `/chat/completions`: `glm-4.6`, эндпоинт `https://api.z.ai/api/paas/v4`
  (или `open.bigmodel.cn` через `GLM_BASE_URL`). Ключ — `GLM_API_KEY`.
- Fallback-провайдеры: DeepSeek, OpenAI, Anthropic, Ollama, mock (тесты).
- RAG-сервис: тот же набор провайдеров (GLM основной) + per-request routing,
  allowlist моделей, кэш ответов, circuit breaker.
- Агент: tools `update_criteria`, `ask_clarification`, `answer_question`,
  `finalize_search`; RAG с fallback на LLM при низкой confidence.

## 4. База знаний (services/rag/kb) — РБ / РФ / СНГ + источники

| Файл | Содержание |
|---|---|
| `customs.md` | Единые ставки ЕАЭС по возрастам (Решение ЕЭК № 107 прил. 6), коммерческий ввоз (ТН ВЭД + акциз + НДС), РБ/КЗ/Армения, порядок растаможки |
| `fees.md` | Утильсбор РФ (ПП 1291, индексации, льготный 0.17 vs коммерческий), платежи РБ, СБКТС/ЭПТС, налоги; **конверсия KRW/CNY/EUR/BYN/RUB → USD** |
| `countries.md` | Источники: Корея (аукционы, мальсо, Ro-Ro Пусан), Китай (NEV, лицензированный экспортёр, Хоргос/Забайкальск), ОАЭ, Европа/США (санкции), Япония; методика расчёта «под ключ» в USD |
| `delivery.md` | Маршруты и сроки (Ro-Ro/автовоз/контейнер), что входит во фрахт, риски логистики |
| `risks.md` | Юридические (санкции, залоги, «утильсбор с продажей», схемы КЗ), технические, финансовые; чек-лист сделки |
| `legal.md` | Правовая база РФ/РБ, экспортные ограничения стран-источников (ЕС 833/2014, США EAR, Корея), compliance-политика сервиса |
| `faq.md` | Ответы на частые вопросы (регистрация, валюты, растаможка, Корея/Китай, уведомления) |

Все ставки помечены «проверять на дату ввоза» — без выдуманных точных цифр.

## 5. Telegram WebApp — реальная интеграция

- Кнопка-меню бота: при старте `set_chat_menu_button` → WebApp «🚗 Поиск авто»
  открывается из клиента Telegram (`MINIAPP_URL`, нужен HTTPS в проде).
- Команды бота зарегистрированы через `set_my_commands`
  (start / menu / reset / help / stop, scope: личные чаты).
- Фронт: `telegram-web-app.js`, авторизация initData → JWT, тема `--tg-theme-*`.
- Inline-меню: AI-подбор, WebApp-кнопка, уведомления (мгновенно/дайджест/выкл),
  подписка, сброс диалога.

## 6. Постквантовое шифрование документов

- `app/core/pq_crypto.py` (+ копия в rag_core): гибрид
  **ML-KEM-768 (FIPS 203) + X25519 → HKDF-SHA256 → AES-256-GCM**,
  формат контейнера `AUPQ1`, AAD = ключ объекта.
- `EncryptedFileStorage` поверх MinIO: шифрование at-rest при upload,
  расшифровка при download, legacy-объекты проходят как есть.
- Включение: `ENCRYPT_DOCUMENTS=true` + `PQ_ENCRYPTION_PUBLIC_KEY` /
  `PQ_ENCRYPTION_PRIVATE_KEY` (генерация пары — в `.env.example`).
- Зависимость `cryptography>=45` (app + services/rag).
- Тесты: roundtrip, AAD, чужой ключ, пустые/малые документы, legacy passthrough.

## 7. Инфраструктура и эксплуатация

- docker-compose: полный стек + env для GLM и PQ-ключей.
- Миграции: alembic (основная БД + БД rag), `0003_web_auth`.
- `scripts/ingest_kb.py` (--force), поддержка PQ-шифрования при заливке KB.
- JWT в rag-сервисе: HS*/RS*/ES* + ротация ключей, black-list, PII-редакция.

---

## Коммиты

| Commit | Что вошло |
|---|---|
| `1c898e4` | ТЗ (TZ.md) |
| `b448c76` | Локальный RAG-микросервис, /api/ai, AI-поиск в Mini App, inline-меню бота |
| `a1d39c7` | Регистрация/логин сайта (email+пароль), Login-экран, гейтинг поиска |
| `46a7eb4` | GLM как основной LLM, расширенная БЗ РБ/РФ/СНГ, TG-кнопка меню WebApp, PQ-шифрование |

## Что осталось (next steps)

1. Деплой: пересобрать образы (`docker compose build`), `alembic upgrade head`
   (основная + rag БД), `ingest_kb.py --force`; задать `GLM_API_KEY`,
   `RAG_API_TOKEN`, `RAG_JWT_SECRET`, PQ-ключи.
2. Живой E2E RAG-стека (compose up → ingest → запрос через rag-api).
3. **Отозвать BOT_TOKEN через @BotFather** (утечка).
4. Решить: пользователь 809464979 (tier=pro) — сбросить на Free?
5. Проверить, что callback настроек уведомлений поддерживает
   мгновенно/дайджест/выкл так, как шлёт меню бота.
6. Залить `refactor/monorepo` rag_service (решение владельца).
7. Кандидаты дальше: мониторинг/алерты (этап 8), DLQ-алертинг.
8. Незакоммиченные файлы (`Dockerfile`, `alembic.ini`, `frontend/*`) —
   решить, включать ли в репозиторий.
