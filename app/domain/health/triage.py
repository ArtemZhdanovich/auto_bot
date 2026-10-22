"""Триаж сбоя парсера: почему источник отдал 0 карточек.

Диагноз определяет лечение, поэтому он — доменное правило, а не деталь
сервиса: бан/капчу лечат прокси и хедерами, упавший сервер — ожиданием,
и только смена вёрстки оправдывает вызов LLM.

Модуль чистый: ни сети, ни БД, ни LLM — только строки HTML/ошибки.
"""

from __future__ import annotations

import re
from typing import Final, Literal

TriageReason = Literal["banned", "server_down", "markup_change"]

BANNED: Final[str] = "banned"
SERVER_DOWN: Final[str] = "server_down"
MARKUP_CHANGE: Final[str] = "markup_change"

# Короче — заглушка/антибот, а не страница со списком объявлений.
MIN_HTML_LEN: Final[int] = 300

# Маркеры антибота/капчи в HTML (сравнение в нижнем регистре):
# кириллица/китайский — WAF-заглушки che168/авито-подобных сайтов,
# латиница — общие случаи.
BAN_MARKERS: Final[tuple[str, ...]] = (
    "captcha",
    "verify",
    "access denied",
    "forbidden",
    "unusual traffic",
    "请完成验证",
    "安全验证",
    "滑动验证",
    "访问过于频繁",
    "punish",
    "waf",
)

# HttpClient ретраит и бросает FetchError строкой "HTTP <code> for <url>".
HTTP_CODE_RE: Final[re.Pattern[str]] = re.compile(r"HTTP (\d{3})")

# Статусы, которые означают бан/геоблок/лимит запросов, а не падение сайта.
BAN_STATUSES: Final[frozenset[int]] = frozenset({401, 403, 406, 429})


def classify_page(html: str) -> TriageReason:
    """Триаж нормально отдавшейся страницы (HTTP 2xx).

    Args:
        html: Тело ответа списка объявлений.

    Returns:
        `banned` — капча/заглушка антибота (короткая страница или маркеры);
        `markup_change` — страница живая, карточек в ней нет.
    """
    low = html.lower()
    if len(html.strip()) < MIN_HTML_LEN:
        return "banned"  # пустышка-страница — типичное поведение антибота
    if any(marker in low for marker in BAN_MARKERS):
        return "banned"
    return "markup_change"


def classify_fetch_error(error: str | None) -> TriageReason:
    """Триаж сетевого сбоя (HttpClient завернул 4xx/5xx в FetchError).

    Args:
        error: Текст ошибки, обычно "HTTP <code> for <url>".

    Returns:
        `banned` — 401/403/406/429 (бан, геоблок, лимит запросов);
        `server_down` — 5xx, таймауты, DNS/коннект: источник лежит.
    """
    match = HTTP_CODE_RE.search(error or "")
    code = int(match.group(1)) if match else None
    if code in BAN_STATUSES:
        return "banned"
    return "server_down"
