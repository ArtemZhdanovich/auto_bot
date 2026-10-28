"""AI-отчёт истории лота (12 §1): факты из чека осмотра + LLM-вывод.

История ДТП живёт текстом в `inspection_reports.summary`: адаптер encar
складывает туда факты record/open («история ДТП: N страховых случаев …»,
«ДТП 2023-03-09 — выплата 890 800 원»). Старые отчёты прошли через
машинный перевод и хранят те же строки по-английски («accident history:
3 insurance cases (for this car: 2 …)») — парсер ниже понимает оба
формата (выборка по реальным данным БД, октябрь 2026).

Сценарий детерминирован: LLM добавляет только «человеческий» абзац-вывод
(2-3 предложения по фактам), падение модели не ломает отчёт — факты
остаются. Готовый payload кэшируется в Redis на 24 часа.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.application.catalog.ports import LLMFactory, UserLookupPort
from app.application.errors import Forbidden, NotFound
from app.application.shared.ports import CachePort
from app.domain.listings.ports import ListingRepository
from app.application.catalog.ports import ListingReadRepository

logger = logging.getLogger(__name__)

# Гость получает 403 (как AI-анализ цены, 06 §5).
HISTORY_GUEST_FORBIDDEN = (
    "history_guest_forbidden: отчёт истории доступен только после входа "
    "или регистрации"
)
NO_HISTORY_DATA = (
    "no_history_data: по этому лоту нет данных для отчёта истории"
)

# Кэш Redis: факты меняются редко (12 §1), TTL 24 часа.
HISTORY_CACHE_PREFIX = "history-report"
HISTORY_CACHE_TTL_SEC = 24 * 60 * 60

SYSTEM_PROMPT = (
    "Ты — опытный автоэксперт по рынку Кореи. Тебе дают факты об истории "
    "конкретного автомобиля (счётчик ДТП, последнее ДТП, владельцы, чек "
    "осмотра). Сделай по этим фактам вывод для покупателя: 2-3 предложения "
    "по-русски — стоит ли смотреть эту машину и на что обратить внимание. "
    "Используй ТОЛЬКО факты из входа, ничего не выдумывай. Без markdown."
)

# --------------------------------------------------------------------------
# Парсеры фактов из summary отчёта (формат адаптера encar + машиноперевод)
# --------------------------------------------------------------------------

# «история ДТП: 2 страховых случаев (…)» / «accident history: 3 insurance
# cases (…)» — счётчик страховых случаев всегда «ключ: число …случаев».
ACCIDENT_TOTAL_RE = re.compile(
    r"(?:история\s+ДТП|страховые\s+случаи"
    r"|accident\s+history|history\s+of\s+(?:accidents|road\s+accidents"
    r"|road\s+traffic|DTAs|DTS|crashes|an\s+accident)"
    r"|road\s+accidents?\s+history"
    r")\s*:\s*(\d+)",
    re.IGNORECASE,
)
# «история ДТП: страховых случаев нет» и его переводы.
ACCIDENT_NONE_RE = re.compile(
    r"(?:история\s+ДТП|accident\s+history|history\s+of\s+(?:accidents"
    r"|road\s+accidents|road\s+traffic|DTAs|DTS|crashes)"
    r"|road\s+accidents?\s+history"
    r")\s*:\s*"
    r"(?:страховых\s+случаев\s+нет|no\s+[\w\s'’]*?"
    r"(?:cases?|accidents?|incidents?|insured))",
    re.IGNORECASE,
)
# Строка одного ДТП: «ДТП 2023-03-09 — выплата 890 800 원» / «accident
# 2023-03-09  payment 89 080 yen». Дата может быть урезана до года.
ACCIDENT_ENTRY_RE = re.compile(
    r"(?:ДТП|accidents?|crashes?|accident\s+(?:records?|history|report))\s+"
    r"(\d{4}(?:-\d{2}){0,2})",
    re.IGNORECASE,
)
# Выплата в строке ДТП («выплата 890 800 원» / «payment 89 080 yen»).
PAYOUT_RE = re.compile(
    r"(?:выплата|payment)s?\s+((?:\d[\d\s.,]*)\d|\d)",
    re.IGNORECASE,
)
# Разбивка вины в счётчике: «по этому авто: 2» и переводы.
MY_FAULT_RE = re.compile(
    r"(?:по\s+этому\s+авто|этим\s+авто)\s*:?\s*(\d+)"
    r"|(?:for|on|by|in|according\s+to|by)\s+this(?:\s+(?:auto|car|case))?"
    r"\s*:?\s*(\d+)|by\s+car\s*:?\s*(\d+)",
    re.IGNORECASE,
)
OTHER_FAULT_RE = re.compile(
    r"(?:по\s+вине\s+других|вине\s+других|у\s+других)\s*:?\s*(\d+)"
    r"|(?:at|by|for|due\s+to|on)\s+the?\s*fault\s+of\s+others\s*:?\s*(\d+)",
    re.IGNORECASE,
)
# «смен владельцев: 2» и переводы («change of owners: 2»).
OWNERS_COUNT_RE = re.compile(
    r"^(?:смен\s+владельцев|change\s+of\s+owners|change\s+owners"
    r"|owner'?s?\s+change)\s*[,:;]?\s*(\d+)",
    re.IGNORECASE,
)
# «первая регистрация: 20230828» / «first registry: 2024-03-27».
FIRST_REG_RE = re.compile(
    r"^(?:первая\s+регистрация|first\s+registr\w*|first\s+registry"
    r"|first\s+register)\s*[,:;]?\s*(\d{8}|\d{4}(?:-\d{2}){1,2}|\d{4})",
    re.IGNORECASE,
)
# Строка чека осмотра с флагом ДТП.
_INSPECTION_CHECK_RE = re.compile(
    r"чек[а-яё\s]*осмотр|check[^\n]{0,30}inspection|inspection[^\n]{0,30}check",
    re.IGNORECASE,
)
_INSPECTION_NO_ACCIDENT_RE = re.compile(
    r"ДТП\s+не\s+отмечены|no\s+[\w\s]*accidents?\s+marked"
    r"|accidents?\s+not\s+marked|unmarked|there\s+are\s+no\s+accidents",
    re.IGNORECASE,
)
_INSPECTION_HAS_ACCIDENT_RE = re.compile(
    r"отмечены\s+ДТП|ДТП\s+отмечены|marked\s+by\s+an?\s+accident",
    re.IGNORECASE,
)
# «вердикт Encar: без ДТП» / «Encar verdict: no accidents».
NO_ACCIDENT_VERDICT_RE = re.compile(
    r"вердикт\s+Encar\s*:\s*без\s+ДТП|Encar\s+verdict\s*:\s*no\s+accident",
    re.IGNORECASE,
)
# «Clean Encar: безаварийная» и переводы («accident free», «accidentless»).
CLEAN_ENCAR_RE = re.compile(
    r"Clean\s+Encar\w*\s*:\s*"
    r"(?:безаварийная|accident[\w\s-]*free|accidentless|non[\w\s-]*accidental)",
    re.IGNORECASE,
)
# Коммерческое/гос использование (только канонический русский формат).
USAGE_COMMERCIAL_RE = re.compile(r"коммерческое\s+использование", re.IGNORECASE)
USAGE_GOVERNMENT_RE = re.compile(r"использование\s+госорганами", re.IGNORECASE)


def _int(raw: str) -> int:
    """Цифры строки → int («890 800», «400,000» → число)."""
    digits = re.sub(r"[^\d]", "", raw)
    return int(digits) if digits else 0


def _normalize_date(raw: str) -> str:
    """«20230828» → «2028-…» нет: 2023-08-28; ISO-даты — как есть."""
    raw = raw.strip()
    if re.fullmatch(r"\d{8}", raw):
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:]}"
    return raw


def _pick_first_group(match: re.Match) -> int | None:
    """Первая непустая группа матча (альтернативы регулярки)."""
    for value in match.groups():
        if value is not None:
            return int(value)
    return None


@dataclass(frozen=True, slots=True)
class AccidentFacts:
    """Что вытащили из summary про ДТП."""

    count: int | None = None
    by_this_car: int | None = None
    by_others: int | None = None
    last_date: str | None = None
    last_payout: int | None = None

    def as_dict(self) -> dict[str, Any]:
        """JSON-представление для DTO (последнее ДТП с описанием)."""
        last: dict[str, Any] | None = None
        if self.last_date:
            description = (
                f"страховая выплата ≈{self.last_payout:,} ₩".replace(",", " ")
                if self.last_payout
                else "без данных о выплате"
            )
            last = {"date": self.last_date, "description": description}
        return {
            "count": self.count,
            "by_this_car": self.by_this_car,
            "by_others": self.by_others,
            "last": last,
        }


def _parse_chunk(chunk: str, accidents: AccidentFacts | None) -> AccidentFacts | None:
    """Разобрать один факт summary; `None` — чанк не про ДТП."""

    acc = accidents or AccidentFacts()

    # Строка одного ДТП: ключевое слово + дата сразу после него (у
    # счётчика между ключом и числом стоит двоеточие и слово, поэтому
    # счётчик в эту ветку не попадает).
    entry = ACCIDENT_ENTRY_RE.search(chunk)
    if entry:
        date = _normalize_date(entry.group(1))
        if acc.last_date is None or date > acc.last_date:
            payout_match = PAYOUT_RE.search(chunk)
            return AccidentFacts(
                count=acc.count,
                by_this_car=acc.by_this_car,
                by_others=acc.by_others,
                last_date=date,
                last_payout=(
                    _int(payout_match.group(1)) if payout_match else acc.last_payout
                ),
            )
        return acc

    total = ACCIDENT_TOTAL_RE.search(chunk)
    if total:
        my = MY_FAULT_RE.search(chunk)
        other = OTHER_FAULT_RE.search(chunk)
        return AccidentFacts(
            count=int(total.group(1)),
            by_this_car=_pick_first_group(my) if my else acc.by_this_car,
            by_others=_pick_first_group(other) if other else acc.by_others,
            last_date=acc.last_date,
            last_payout=acc.last_payout,
        )

    if ACCIDENT_NONE_RE.search(chunk):
        return AccidentFacts(
            count=0,
            by_this_car=acc.by_this_car,
            by_others=acc.by_others,
            last_date=acc.last_date,
            last_payout=acc.last_payout,
        )
    return acc if accidents is not None else None


def parse_accident_facts(*texts: str | None) -> AccidentFacts:
    """Собрать факты ДТП из текстов (summary, заметки marks/items).

    Args:
        texts: Произвольное число текстов с фактами (summary отчёта,
            заметки чека и т.п.); `None` пропускается.

    Returns:
        Накопленные факты; «нет данных» — все поля `None`.
    """
    acc: AccidentFacts | None = None
    for text in texts:
        if not text:
            continue
        for chunk in re.split(r"\s*[·•]\s*", text):
            acc = _parse_chunk(chunk.strip(), acc)
    return acc or AccidentFacts()


def collect_history_facts(card: Any, report: Any, owner_card: Any = None) -> dict:
    """Детерминированная структурная часть отчёта (12 §1а).

    Args:
        card: `ListingCard` (make/model/year/mileage) из `cards_by_ids`.
        report: `InspectionView` чека осмотра или `None`.
        owner_card: Активная карточка с owner_type/owner_history (best-effort).

    Returns:
        Словарь фактов для DTO (JSON-безопасные типы).
    """
    summary = getattr(report, "summary", None) if report is not None else None
    marks = list(getattr(report, "marks", None) or []) if report is not None else []
    items = list(getattr(report, "items", None) or []) if report is not None else []
    notes = [
        str(mark.get("note"))
        for mark in marks
        if isinstance(mark, dict) and mark.get("note")
    ] + [
        str(item.get("note"))
        for item in items
        if isinstance(item, dict) and item.get("note")
    ]

    accidents = parse_accident_facts(summary, *notes)

    inspection_flag: bool | None = None
    owners_count: int | None = None
    first_registration: str | None = None
    usage: list[str] = []
    clean_encar = False
    no_accident_verdict = False
    for chunk in re.split(r"\s*[·•]\s*", summary or ""):
        chunk = chunk.strip()
        if not chunk:
            continue
        if _INSPECTION_CHECK_RE.search(chunk) and re.search(
            r"ДТП|accident|crash|DTAs|DTS", chunk, re.IGNORECASE
        ):
            if _INSPECTION_NO_ACCIDENT_RE.search(chunk):
                inspection_flag = False
            elif _INSPECTION_HAS_ACCIDENT_RE.search(chunk):
                inspection_flag = True
        owners = OWNERS_COUNT_RE.search(chunk)
        if owners and owners_count is None:
            owners_count = int(owners.group(1))
        first = FIRST_REG_RE.search(chunk)
        if first:
            candidate = _normalize_date(first.group(1))
            # ISO-форма (из record/open) информативнее компактной.
            if first_registration is None or (
                "-" in candidate and "-" not in first_registration
            ):
                first_registration = candidate
        if USAGE_COMMERCIAL_RE.search(chunk):
            usage.append("commercial")
        if USAGE_GOVERNMENT_RE.search(chunk):
            usage.append("government")
        if CLEAN_ENCAR_RE.search(chunk):
            clean_encar = True
        if NO_ACCIDENT_VERDICT_RE.search(chunk):
            no_accident_verdict = True

    issues = [
        {
            "key": str(item.get("key")),
            "status": str(item.get("status")),
            "note": item.get("note"),
        }
        for item in items
        if isinstance(item, dict) and item.get("status") not in (None, "ok")
    ]

    owner_type = getattr(owner_card, "owner_type", None)
    owner_history = list(getattr(owner_card, "owner_history", None) or [])

    facts: dict[str, Any] = {
        "car": {
            "make": getattr(card, "make", None),
            "model": getattr(card, "model", None),
            "year": getattr(card, "year", None),
            "mileage_km": getattr(card, "mileage_km", None),
        },
        "accidents": accidents.as_dict(),
        # Пробег подтверждён, если есть чек осмотра (12 §1).
        "mileage_confirmed": report is not None,
        "owner_type": owner_type,
        "owner_history": [
            dict(event) for event in owner_history if isinstance(event, dict)
        ],
    }
    if inspection_flag is not None:
        facts["inspection_accidents"] = inspection_flag
    if clean_encar:
        facts["clean_encar"] = True
    if no_accident_verdict:
        facts["no_accident_verdict"] = True
    if owners_count is not None:
        facts["owners_count"] = owners_count
    if usage:
        facts["usage"] = usage
    if first_registration:
        facts["first_registration"] = first_registration
    if report is not None:
        facts["inspection"] = {
            "grade": getattr(report, "grade", None),
            "inspected_at": (
                getattr(report, "inspected_at", None).isoformat()
                if getattr(report, "inspected_at", None) is not None
                else None
            ),
            "marks_count": len(marks),
            "issues": issues,
        }
    return facts


@dataclass(frozen=True, slots=True)
class HistoryReportView:
    """Ответ GET /api/listings/{id}/history-report (12 §1)."""

    listing_id: int
    facts: dict
    llm_summary: str | None
    generated_at: str


class BuildHistoryReport:
    """Сборка отчёта истории: факты из БД → LLM-абзац → кэш 24ч."""

    def __init__(
        self,
        listings: ListingRepository,
        reports: ListingReadRepository,
        users: UserLookupPort,
        llm_factory: LLMFactory,
        cache: CachePort,
    ) -> None:
        self._listings = listings
        self._reports = reports
        self._users = users
        self._llm_factory = llm_factory
        self._cache = cache

    async def execute(self, user_id: int, listing_id: int) -> HistoryReportView:
        """Собрать отчёт (кэш → факты → LLM-вывод).

        Raises:
            NotFound: Лота нет, пользователя нет или нет данных истории.
            Forbidden: Гостевой аккаунт.
        """
        cards = await self._listings.cards_by_ids([listing_id])
        card = cards.get(listing_id)
        if card is None:
            raise NotFound("Listing not found")

        profile = await self._users.get(user_id)
        if profile is None:
            raise NotFound("User not found")
        if profile.is_guest:
            logger.info(
                "history_report_guest_forbidden user_id=%s listing_id=%s",
                user_id,
                listing_id,
            )
            raise Forbidden(HISTORY_GUEST_FORBIDDEN)

        cached = await self._cached(listing_id)
        if cached is not None:
            return cached

        report = await self._reports.get_report(listing_id)
        owner_card = None
        try:
            owner_card = await self._reports.get_active(listing_id)
        except Exception:  # noqa: BLE001 — owner-контекст не критичен
            logger.warning(
                "history_report_owner_read_failed listing_id=%s", listing_id
            )

        facts = collect_history_facts(card, report, owner_card)
        if report is None and not facts["owner_history"]:
            raise NotFound(NO_HISTORY_DATA)

        llm_summary = await self._llm_paragraph(listing_id, facts)
        view = HistoryReportView(
            listing_id=listing_id,
            facts=facts,
            llm_summary=llm_summary,
            generated_at=datetime.now(UTC).isoformat(),
        )
        await self._remember(listing_id, view)
        logger.info(
            "history_report_built listing_id=%s llm=%s", listing_id, llm_summary is not None
        )
        return view

    async def _cached(self, listing_id: int) -> HistoryReportView | None:
        """Кэш Redis 24ч; лежачий Redis не роняет отчёт (best-effort)."""
        try:
            raw = await self._cache.get(f"{HISTORY_CACHE_PREFIX}:{listing_id}")
        except Exception:  # noqa: BLE001 — кэш не критичен
            logger.warning("history_report_cache_get_failed", exc_info=True)
            return None
        if not raw:
            return None
        try:
            data = json.loads(raw)
            return HistoryReportView(
                listing_id=data["listing_id"],
                facts=data["facts"],
                llm_summary=data.get("llm_summary"),
                generated_at=data["generated_at"],
            )
        except (KeyError, TypeError, ValueError):
            logger.warning("history_report_cache_corrupted listing_id=%s", listing_id)
            return None

    async def _remember(self, listing_id: int, view: HistoryReportView) -> None:
        """Положить payload в Redis на 24 часа (best-effort)."""
        try:
            await self._cache.set(
                f"{HISTORY_CACHE_PREFIX}:{listing_id}",
                json.dumps(
                    {
                        "listing_id": view.listing_id,
                        "facts": view.facts,
                        "llm_summary": view.llm_summary,
                        "generated_at": view.generated_at,
                    },
                    ensure_ascii=False,
                ),
                HISTORY_CACHE_TTL_SEC,
            )
        except Exception:  # noqa: BLE001 — кэш не критичен
            logger.warning("history_report_cache_set_failed", exc_info=True)

    async def _llm_paragraph(
        self, listing_id: int, facts: dict[str, Any]
    ) -> str | None:
        """Абзац-вывод по фактам; сбой модели → без абзаца (12 §1б)."""
        try:
            llm = self._llm_factory()
            reply = await llm.complete(
                [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": (
                            "Факты об истории автомобиля:\n"
                            + json.dumps(facts, ensure_ascii=False, default=str)
                        ),
                    },
                ]
            )
        except Exception:  # noqa: BLE001 — LLM-абзац опционален
            logger.warning(
                "history_report_llm_failed listing_id=%s", listing_id, exc_info=True
            )
            return None
        text = str(getattr(reply, "text", "") or "").strip()
        return text or None
