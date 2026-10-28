# AI-слой: LLM, анализ, история, описания, RAG

## LLM-клиент (`app/infrastructure/ai`)

`LLMPort.complete(messages)` — GLM (z.ai) с таймаутами/ретраями;
фабрика `LLMFactory` инжектится в сценарии; сбой → фолбэк на
статистику (для анализа) или 503 (для генераций без фолбэка).

## AI-контур продукта

- **Анализ цены** (`application/pricing`): медиана сопоставимых
  (±2 года, ≤30) + LLM-вердикт deal/fair/overpriced + факторы;
  Business — «глубокий» разбор (чек осмотра в контексте). Квоты:
  free 1/нед, pro 30/день, business безлимит.
- **Отчёт истории** (`application/listings/history_report.py`):
  факт-парсер ДТП по отчётам encar (счётчик/вина/последнее ДТП+
  выплата/владельцы/регистрация; понимает и канонический русский,
  и машиннопереведённые строки) + LLM-вывод; кэш 24ч.
- **Похожие** (`application/analysis/similar.py`): SQL-кандидаты
  (модель ±3 года, цена ±35%) → LLM-реранк топ-5 с reason.
- **Генерация описаний** (`application/market/description.py`):
  из полей черновика, RU 400–800 зн., только факты; free 5/день.
- **Качество объявления** (`application/market/quality.py`): чистая
  функция (фото/описание/цена vs медиана → score+tips).
- **Выжимка диалогов** (`application/chat/summary.py`): последние 50
  сообщений → ≤600 зн. для оператора/партнёра; кэш 1ч.
- **RAG-бот поддержки** (`infrastructure/services/support_bot.py`):
  support-чаты: RAG (Qdrant, сервис services/rag) → LLM-фолбэк;
  bot_paused при ответе оператора; free 15 сообщ./день; typing-события.
- **AI-поиск** (`application/dialog`): диалоговый агент (критерии →
  поиск по БД → кнопки), квоты по тарифу.

## Микросервисы

- **translate** (NLLB) — docs/SOURCES.md.
- **rag** (FastAPI + Qdrant + rabbit worker) — гибридный поиск по
  базе знаний, `/api/v1/ask` с Bearer-токеном.
