"""Сценарий ParserDoctor: триаж → LLM-предложение → проверка → применение.

Ремонт SSR-парсера, который внезапно отдал 0 карточек:

0. Триаж живого ответа источника (доменные правила `domain/health/triage`):
   бан/капча и упавший сервер лечатся не селекторами — LLM не зовём;
1. LLM предлагает новые CSS-селекторы (второй рубеж, только смена вёрстки);
2. предложение проверяется на том же HTML — невалидное не применяется;
3. `auto` — пишем конфиг и перезагружаем адаптер, `suggest` — только алерт.

Ни один сбой (LLM, мусорный JSON, БД) не выходит наружу: статус в результате
и алерт владельцу. Cooldown и лок «ремонт уже идёт» ставит сценарий через
порт блокировок, чтобы не жечь токены дублями.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

from app.application.dto.doctor import DoctorResult, ListPage
from app.application.parser_doctor.ports import (
    AlertPort,
    LockPort,
    ParserProbe,
    SourceConfigRepository,
    SourceRegistryPort,
)
from app.application.ports import LLMPort
from app.application.shared.ports import UnitOfWork
from app.domain.health.triage import (
    MARKUP_CHANGE,
    classify_fetch_error,
    classify_page,
)

logger = logging.getLogger(__name__)

SELECTOR_KEYS = ("card_selector", "title_selector", "price_selector", "link_selector")
DOCTOR_MODES = ("suggest", "auto")
COOLDOWN_SEC = 6 * 3600
# Лок «ремонт уже идёт»: длинный CoT-запрос к LLM (GLM 4.6) занимает минуты;
# без него параллельный воркер (повторный fetch с пустой выдачей) успевает
# запустить второй цикл и сжечь второй LLM-запрос. TTL с запасом больше
# максимального таймаута LLM; снимается в finally, падение процесса чистит TTL.
REPAIR_LOCK_SEC = 30 * 60
HTML_LIMIT = 15_000  # сколько HTML показываем LLM

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


class RepairParser:
    """Диагностика и ремонт SSR-парсера источника."""

    def __init__(
        self,
        sources: SourceConfigRepository,
        probe: ParserProbe,
        llm_factory: Callable[[], LLMPort],
        alerts: AlertPort,
        uow: UnitOfWork,
        *,
        locks: LockPort | None = None,
        registry: SourceRegistryPort | None = None,
    ) -> None:
        self._sources = sources
        self._probe = probe
        self._llm_factory = llm_factory
        self._alerts = alerts
        self._uow = uow
        self._locks = locks
        self._registry = registry

    async def execute(self, source_name: str) -> DoctorResult:
        """Поставить диагноз и, если нужно, отремонтировать парсер.

        Args:
            source_name: Имя источника (`sources.name`).

        Returns:
            Результат со статусом: `repaired` / `suggested` / `cooldown` /
            `banned` / `server_down` / `invalid` / `llm_error` и т.п.
        """
        source = await self._sources.get(source_name)
        if source is None:
            return DoctorResult(source=source_name, status="no_source")
        config = dict(source.config)
        mode = config.get("doctor_mode", "off")
        if mode not in DOCTOR_MODES:
            return DoctorResult(source=source_name, status="disabled")

        old_selectors = {key: config.get(key) for key in SELECTOR_KEYS}

        # 0) живой ответ источника + триаж причины: бан, падение сервера и
        #    смена вёрстки лечатся по-разному — LLM зовём только в последнем
        page = await self._probe.fetch_list_page(config)
        triage = (
            classify_fetch_error(page.error)
            if page.status is None
            else classify_page(page.html or "")
        )
        if triage != MARKUP_CHANGE:
            return await self._report_triage(source_name, triage, page)

        # Cooldown ставим только перед LLM-ремонтом: бан и падение сервера
        # триажатся при каждом запуске врача, без «остывания»
        if self._locks is None:
            logger.warning("parser_doctor_no_redis_no_lock: %s", source_name)
            return await self._repair_with_llm(
                source_name, config, mode, old_selectors, page.html or ""
            )

        if not await self._locks.acquire(f"doctor:{source_name}", COOLDOWN_SEC):
            return DoctorResult(source=source_name, status="cooldown")
        if not await self._locks.acquire(
            f"doctor:running:{source_name}", REPAIR_LOCK_SEC
        ):
            # Ремонт этого источника уже идёт в другом воркере — второй
            # LLM-запрос не нужен (фидбек: «не жечь токены дублями»).
            logger.info("parser_doctor_already_running: %s", source_name)
            return DoctorResult(source=source_name, status="already_running")
        try:
            return await self._repair_with_llm(
                source_name, config, mode, old_selectors, page.html or ""
            )
        finally:
            await self._locks.release(f"doctor:running:{source_name}")

    async def _repair_with_llm(
        self,
        source_name: str,
        config: dict[str, Any],
        mode: str,
        old_selectors: dict[str, Any],
        html: str,
    ) -> DoctorResult:
        """LLM-ремонт: селекторы → живая проверка → применить/предложить."""
        prompt = PROMPT.format(
            selectors=json.dumps(old_selectors, ensure_ascii=False),
            html=html[:HTML_LIMIT],
        )
        llm = self._llm_factory()
        try:
            result = await llm.complete([{"role": "user", "content": prompt}])
        except Exception as exc:  # noqa: BLE001 — LLMError и любой сбой сети
            await self._alerts.send(
                f"🩺 ParserDoctor {source_name}: LLM недоступен, "
                f"парсер остался сломан ({exc})",
                dedup_key=f"doctor_llm:{source_name}",
            )
            return DoctorResult(source=source_name, status="llm_error")

        proposal = extract_json(getattr(result, "text", None))
        new_selectors = {
            key: proposal[key] for key in SELECTOR_KEYS if proposal and key in proposal
        }
        if len(new_selectors) != len(SELECTOR_KEYS):
            return DoctorResult(source=source_name, status="llm_bad_output")

        # Валидация на том же HTML — только проверенное применяется
        merged = {**config, **new_selectors}
        parsed = self._probe.validate_selectors(merged, html)
        if parsed is None:
            await self._alerts.send(
                f"🩺 ParserDoctor {source_name}: предложение LLM не прошло "
                f"валидацию, конфиг не тронут. Предложено: "
                f"{json.dumps(new_selectors, ensure_ascii=False)}",
                dedup_key=f"doctor_invalid:{source_name}",
            )
            return DoctorResult(
                source=source_name, status="invalid", proposed=new_selectors
            )

        if mode == "auto":
            await self._sources.save_config(source_name, merged)
            await self._uow.commit()
            if self._registry is not None:
                await self._registry.reload(source_name)
            await self._alerts.send(
                f"🩺 ParserDoctor {source_name}: парсер отремонтирован "
                f"({parsed} карточек на проверке).\n"
                f"Было: {json.dumps(old_selectors, ensure_ascii=False)}\n"
                f"Стало: {json.dumps(new_selectors, ensure_ascii=False)}",
                dedup_key=f"doctor_repaired:{source_name}",
            )
            logger.warning(
                "parser_repaired: source=%s selectors=%s", source_name, new_selectors
            )
            return DoctorResult(source=source_name, status="repaired", items=parsed)

        await self._alerts.send(
            f"🩺 ParserDoctor {source_name}: парсер отдаёт 0 карточек. Предложение "
            f"LLM (проверено на живом HTML, {parsed} карточек — примените через "
            f"admin API):\n{json.dumps(new_selectors, ensure_ascii=False)}",
            dedup_key=f"doctor_suggest:{source_name}",
        )
        return DoctorResult(source=source_name, status="suggested", items=parsed)

    async def _report_triage(
        self, source_name: str, triage: str, page: ListPage
    ) -> DoctorResult:
        """Алерт с причиной без вызова LLM: бан / недоступен / страница переехала."""
        details = (
            f"HTTP {page.status}"
            if page.status is not None
            else f"сеть: {(page.error or '')[:200]}"
        )
        if triage == "banned":
            text = (
                f"🚫 ParserDoctor {source_name}: похоже на бан/геоблок/капчу "
                f"({details}). Селекторы ни при чём — нужна ротация "
                f"прокси/хедеров, LLM не звал."
            )
        elif triage == "server_down":
            text = (
                f"🛑 ParserDoctor {source_name}: источник недоступен ({details}). "
                f"Ждём восстановления — LLM-ремонт не запускался."
            )
        else:  # 404/410 и т.п. — страница списка переехала
            text = (
                f"⚠️ ParserDoctor {source_name}: страница списка отвечает "
                f"{details}. Проверьте base_url/list_path — селекторами это "
                f"не лечится."
            )
        logger.warning(
            "parser_doctor_triage: source=%s triage=%s (%s)",
            source_name,
            triage,
            details,
        )
        await self._alerts.send(
            text, dedup_key=f"doctor_triage_{triage}:{source_name}"
        )
        return DoctorResult(
            source=source_name,
            status=triage,
            http_status=page.status,
            error=page.error,
        )
