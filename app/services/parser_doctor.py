"""ParserDoctor — ИИ-ремонтник SSR-парсеров.

Когда парсер внезапно отдаёт 0 карточек, врач сначала определяет ПРИЧИНУ
(триаж) — лечатся они по-разному:
0. Триаж живого ответа источника:
   - banned       — 401/403/406/429, капча-маркеры или пустышка-страница:
                    селекторы ни при чём, нужен прокси/хедеры, LLM не зовём;
   - server_down  — 5xx, таймауты, ошибки сети/коннекта: ждём восстановления,
                    LLM не зовём;
   - markup_change — страница отдалась нормально, но карточек 0: это смена
                    вёрстки, зовём LLM.
1. Забирает живой HTML страницы списка.
2. Просит LLM (GLM) предложить новые CSS-селекторы (card/title/price/link).
3. **Валидирует** предложение локально: парсит тот же HTML новым адаптером,
   требует >= MIN_ITEMS карточек с уникальными ID и ценами у большинства.
4. mode='auto'  → обновляет sources.config + reload адаптера в реестре;
   mode='suggest' → только алерт владельцу с предложенными селекторами.

Безопасность (принцип «never break business flow»):
- Любая ошибка (LLM недоступен, мусорный JSON, невалидные селекторы)
  не бросается наружу — лог + статус в результате + алерт при auto.
- Cooldown в Redis (doctor:{source}, 6 ч) — LLM-вызовы стоят денег; ставится
  ТОЛЬКО перед LLM-ремонтом: бан и падение сервера триажатся каждый раз.
- Валидация на реальном HTML — LLM не может «сломать» парсер плохим
  селектором: не прошёл проверку → конфиг не меняется.
"""

import json
import logging
import re
from datetime import UTC, datetime
from typing import Any

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.registry import AdapterRegistry
from app.core.config import get_settings
from app.db.models import Source
from app.db.session import session_factory
from app.services.alerting import send_alert

logger = logging.getLogger(__name__)

SELECTOR_KEYS = ("card_selector", "title_selector", "price_selector", "link_selector")
MIN_ITEMS = 3          # минимум карточек, чтобы принять ремонт
MIN_PRICE_RATE = 0.5   # доля карточек с ценой
HTML_LIMIT = 15_000    # сколько HTML показываем LLM
MIN_HTML_LEN = 300     # короче — заглушка/антибот, а не список объявлений
COOLDOWN_SEC = 6 * 3600

# Маркеры антибота/капчи в HTML (сравнение в нижнем регистре):
# chinese — che168/авито-подобные WAF-заглушки, латиница — общие случаи
BAN_MARKERS = (
    "captcha", "verify", "access denied", "forbidden", "unusual traffic",
    "请完成验证", "安全验证", "滑动验证", "访问过于频繁", "punish", "waf",
)
# HttpClient ретраит и бросает FetchError строкой "HTTP <code> for <url>"
HTTP_CODE_RE = re.compile(r"HTTP (\d{3})")

PROMPT = """Ты помогаешь чинить парсер HTML-страницы со списком автомобилей.
Текущие CSS-селекторы перестали работать — сайт сменил вёрстку.
Ниже фрагмент живого HTML страницы. Проанализируй структуру и верни НОВЫЕ селекторы.

Верни ТОЛЬКО валидный JSON без markdown и пояснений, вида:
{{"card_selector": "...", "title_selector": "...", "price_selector": "...", "link_selector": "..."}}

Требования:
- card_selector — выбирает карточки объявлений (несколько элементов);
- title_selector / price_selector / link_selector — внутри одной карточки;
- link_selector должен иметь href на страницу объявления (обычно <a>).

Текущие (сломанные) селекторы: {selectors}

HTML страницы:
{html}"""


def extract_json(text: str | None) -> dict[str, Any] | None:
    """Достать JSON-объект из ответа LLM (терпит ```-заборы и мусор вокруг)."""
    if not text:
        return None
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def classify_page(html: str) -> str:
    """Триаж нормально отдавшейся страницы (HTTP 2xx).

    banned — капча/заглушка антибота (короткая страница или маркеры);
    markup_change — страница живая, карточек в ней нет: смена вёрстки.
    """
    low = html.lower()
    if len(html.strip()) < MIN_HTML_LEN:
        return "banned"  # пустышка-страница — типичное поведение антибота
    if any(marker in low for marker in BAN_MARKERS):
        return "banned"
    return "markup_change"


def classify_fetch_error(error: str | None) -> str:
    """Триаж сетевого сбоя (HttpClient завернул 4xx/5xx в FetchError).

    banned — 401/403/406/429 (бан, геоблок, лимит запросов);
    server_down — 5xx, таймауты, DNS/коннект: источник лежит.
    """
    match = HTTP_CODE_RE.search(error or "")
    code = int(match.group(1)) if match else None
    if code in (401, 403, 406, 429):
        return "banned"
    return "server_down"


def validate_selectors(cls: type, config: dict[str, Any], html: str) -> int | None:
    """Спарсить html адаптером с новыми селекторами; вернёт число карточек.

    None — селекторы структурно невалидны (0 карточек / дубли ID / мало цен).
    """
    probe = cls(source_name="doctor-probe", config=config)
    items = probe._parse(html)  # noqa: SLF001 — синхронный парс без сети
    if len(items) < MIN_ITEMS:
        return None
    ids = [item.source_id for item in items]
    if len(set(ids)) != len(ids):
        return None
    with_price = sum(1 for item in items if item.price is not None)
    if with_price / len(items) < MIN_PRICE_RATE:
        return None
    return len(items)


async def repair_parser(
    source_name: str,
    adapter: Any,
    registry: AdapterRegistry | None = None,
    *,
    session: AsyncSession | None = None,
    redis: Redis | None = None,
    llm: Any | None = None,
) -> dict[str, Any]:
    """Диагностика и ремонт SSR-парсера источника. Не бросает исключений.

    session/redis/llm — точки инъекции для тестов; в воркере создаются сами.
    """
    own_session = session is None
    session = session or session_factory()
    try:
        return await _repair(source_name, adapter, registry, session, redis, llm)
    except Exception as exc:  # noqa: BLE001 — врач не должен ронять фетч
        logger.exception("parser_doctor_failed: %s", source_name)
        return {"source": source_name, "status": "error", "error": str(exc)}
    finally:
        if own_session:
            await session.close()


async def _repair(
    source_name: str,
    adapter: Any,
    registry: AdapterRegistry | None,
    session: AsyncSession,
    redis: Redis | None,
    llm: Any | None,
) -> dict[str, Any]:
    source = await session.get(Source, source_name)
    if source is None:
        return {"source": source_name, "status": "no_source"}
    config: dict[str, Any] = source.config or {}
    mode = config.get("doctor_mode", "off")
    if mode not in ("suggest", "auto"):
        return {"source": source_name, "status": "disabled"}

    old_selectors = {key: config.get(key) for key in SELECTOR_KEYS}

    # 0) живой ответ источника + триаж причины: бан, падение сервера и
    #    смена вёрстки лечатся по-разному — LLM зовём только в последнем случае
    status, html, fetch_error = await _fetch_list_page(adapter, config)
    triage = classify_fetch_error(fetch_error) if status is None else classify_page(html)
    if triage != "markup_change":
        return await _triage_result(source_name, triage, status, fetch_error)

    # Cooldown ставим только перед LLM-ремонтом: бан/падение сервера
    # триажится при каждом запуске врача, без «остывания»
    if redis is not None:
        got = await redis.set(f"doctor:{source_name}", "1", nx=True, ex=COOLDOWN_SEC)
        if not got:
            return {"source": source_name, "status": "cooldown"}

    # 1) HTML уже получен на шаге триажа

    # 2) LLM предлагает новые селекторы
    if llm is None:
        from app.ai.llm_client import build_llm_client

        llm = build_llm_client(get_settings())
    prompt = PROMPT.format(selectors=json.dumps(old_selectors, ensure_ascii=False),
                           html=html[:HTML_LIMIT])
    try:
        result = await llm.complete([{"role": "user", "content": prompt}])
    except Exception as exc:  # noqa: BLE001 — LLMError и любой сбой сети
        await send_alert(
            f"🩺 ParserDoctor {source_name}: LLM недоступен, парсер остался сломан ({exc})",
            dedup_key=f"doctor_llm:{source_name}",
        )
        return {"source": source_name, "status": "llm_error"}
    proposal = extract_json(result.text)
    new_selectors = {k: proposal[k] for k in SELECTOR_KEYS if proposal and k in proposal}
    if len(new_selectors) != len(SELECTOR_KEYS):
        return {"source": source_name, "status": "llm_bad_output"}

    # 3) валидация на том же HTML — только проверенное применяется
    merged = {**config, **new_selectors}
    parsed = validate_selectors(type(adapter), merged, html)
    if parsed is None:
        await send_alert(
            f"🩺 ParserDoctor {source_name}: предложение LLM не прошло валидацию, "
            f"конфиг не тронут. Предложено: {json.dumps(new_selectors, ensure_ascii=False)}",
            dedup_key=f"doctor_invalid:{source_name}",
        )
        return {"source": source_name, "status": "invalid", "proposed": new_selectors}

    # 4) применяем / предлагаем
    if mode == "auto":
        source.config = merged
        source.updated_at = datetime.now(UTC)
        await session.commit()
        if registry is not None:
            await registry.reload_source(source_name)
        await send_alert(
            f"🩺 ParserDoctor {source_name}: парсер отремонтирован "
            f"({parsed} карточек на проверке).\n"
            f"Было: {json.dumps(old_selectors, ensure_ascii=False)}\n"
            f"Стало: {json.dumps(new_selectors, ensure_ascii=False)}",
            dedup_key=f"doctor_repaired:{source_name}",
        )
        logger.warning("parser_repaired: source=%s selectors=%s", source_name, new_selectors)
        return {"source": source_name, "status": "repaired", "items": parsed}

    await send_alert(
        f"🩺 ParserDoctor {source_name}: парсер отдаёт 0 карточек. Предложение LLM "
        f"(проверено на живом HTML, {parsed} карточек — примените через admin API):\n"
        f"{json.dumps(new_selectors, ensure_ascii=False)}",
        dedup_key=f"doctor_suggest:{source_name}",
    )
    return {"source": source_name, "status": "suggested", "items": parsed}


async def _triage_result(
    source_name: str, triage: str, status: int | None, error: str | None
) -> dict[str, Any]:
    """Алерт с причиной без вызова LLM: бан / недоступен / страница переехала."""
    details = f"HTTP {status}" if status is not None else f"сеть: {(error or '')[:200]}"
    if triage == "banned":
        text = (
            f"🚫 ParserDoctor {source_name}: похоже на бан/геоблок/капчу ({details}). "
            f"Селекторы ни при чём — нужна ротация прокси/хедеров, LLM не звал."
        )
    elif triage == "server_down":
        text = (
            f"🛑 ParserDoctor {source_name}: источник недоступен ({details}). "
            f"Ждём восстановления — LLM-ремонт не запускался."
        )
    else:  # http_error: 404/410 и т.п. — страница списка переехала
        text = (
            f"⚠️ ParserDoctor {source_name}: страница списка отвечает {details}. "
            f"Проверьте base_url/list_path — селекторами это не лечится."
        )
    logger.warning(
        "parser_doctor_triage: source=%s triage=%s (%s)", source_name, triage, details
    )
    await send_alert(text, dedup_key=f"doctor_triage_{triage}:{source_name}")
    return {
        "source": source_name,
        "status": triage,
        **({"http_status": status} if status is not None else {}),
        **({"error": error} if error else {}),
    }


async def _fetch_list_page(
    adapter: Any, config: dict[str, Any]
) -> tuple[int | None, str | None, str | None]:
    """Живой HTML страницы списка тем же HttpClient (прокси/ретраи).

    Возвращает (status_code, html, error). Сетевой сбой → (None, None, err):
    raise_for_status в HttpClient превращает 4xx/5xx в FetchError со строкой
    "HTTP <code> for <url>" — код вытаскиваем для триажа бан/недоступен.
    """
    from app.adapters.che168 import HEADERS

    base_url = str(config.get("base_url", "")).rstrip("/")
    list_path = str(config.get("list_path", ""))
    if not base_url:
        return None, None, "no base_url in source config"
    try:
        resp = await adapter.http.get(base_url + list_path, headers=HEADERS)
    except Exception as exc:  # noqa: BLE001 — триажим любую сетевую ошибку
        logger.info("parser_doctor_fetch_failed: %s", exc)
        return None, None, str(exc)
    return resp.status_code, resp.text, None
