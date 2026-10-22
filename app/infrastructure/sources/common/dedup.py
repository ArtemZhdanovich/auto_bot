"""Дедупликация (ТЗ п. 6.10).

- Внутри источника: уникальный индекс `(source, source_id)` в БД.
- Между источниками: ключ по VIN или fuzzy-хэш, TTL 48 часов в Redis.

Ключи версионированы: при изменении состава/нормализации ключа нужно
поднять `_DEDUP_VERSION` — старые ключи истекут за TTL, ручная миграция
не требуется.
"""

import hashlib
import logging
from typing import Any

from redis.asyncio import Redis

from app.domain.listings.unified import UnifiedListing

logger = logging.getLogger(__name__)

DEDUP_TTL_SEC = 48 * 60 * 60  # 48 часов
_DEDUP_VERSION = "v3"  # bump при изменении состава/normalization ключей
# TECHDEBT 3.2: пробег в fuzzy-ключе округляется до кратного 1000 км —
# счётчик пробега гуляет между заходами парсера на единицы км.
_MILEAGE_ROUND_KM = 1000

# Атомарно: если ЛЮБОЙ из ключей существует — вернуть 0 и ничего не менять;
# иначе выставить ВСЕ ключи с TTL и вернуть 1. Без этого частичный SET
# «отравлял» бы ключи для отклонённых объявлений (потеря данных).
_CLAIM_SCRIPT = """
for i = 1, #KEYS do
    if redis.call("EXISTS", KEYS[i]) == 1 then
        return 0
    end
end
for i = 1, #KEYS do
    redis.call("SET", KEYS[i], "1", "EX", ARGV[1])
end
return 1
"""

_RELEASE_SCRIPT = """
local deleted = 0
for i = 1, #KEYS do
    deleted = deleted + redis.call("DEL", KEYS[i])
end
return deleted
"""


def _norm_str(value: str | None) -> str:
    """Нормализует строковое поле: trim, lower, коллапс внутренних пробелов.

    Args:
        value: Исходное значение или `None`.

    Returns:
        Нормализованная строка; `"-"` для `None`/пустой строки.
    """
    if value is None:
        return "-"
    normalized = " ".join(value.strip().lower().split())
    return normalized or "-"


def _norm_int(value: Any) -> str:
    """Канонизирует числовое поле в целое.

    `Decimal("50000.0")` и `Decimal("50000")` дают один и тот же ключ.

    Args:
        value: Исходное значение (int/float/Decimal/str/None).

    Returns:
        Строка целого числа; `"-"` для `None`/невалидного значения.
    """
    if value is None:
        return "-"
    try:
        return str(int(value))
    except (TypeError, ValueError):
        return "-"


def _norm_price(value: Any) -> str:
    """Канонизирует цену: округление до целого доллара, явное поведение.

    Раньше использовался `int(price)` (truncation) — `$19,999.99` и
    `$20,000.00` давали разные ключи, а `$20,000.01` и `$20,000.99` — один.
    `round()` семантически корректнее для «цены в целых долларах».

    Args:
        value: Исходное значение цены (int/float/Decimal/str/None).

    Returns:
        Строка целого числа; `"-"` для `None`/невалидного значения.
    """
    if value is None:
        return "-"
    try:
        return str(int(round(float(value))))
    except (TypeError, ValueError, OverflowError):
        return "-"


def _norm_mileage(value: Any) -> str:
    """Канонизирует пробег: целое, округлённое до кратного 1000 км.

    TECHDEBT 3.2: парсер может прочитать 84 350 км в одном заходе и
    84 410 км в следующем — без округления это «два разных объявления».

    Args:
        value: Исходное значение пробега (int/float/Decimal/str/None).

    Returns:
        Строка целого, кратного `_MILEAGE_ROUND_KM`; `"-"` для
        `None`/невалидного значения.
    """
    if value is None:
        return "-"
    try:
        km = int(round(float(value)))
    except (TypeError, ValueError, OverflowError):
        return "-"
    rounded = int(round(km / _MILEAGE_ROUND_KM)) * _MILEAGE_ROUND_KM
    return str(rounded)


def vin_key(vin: str) -> str:
    """Redis-ключ дедупа по VIN.

    Args:
        vin: VIN (непустой после `.strip()`).

    Returns:
        Ключ вида `dedup:{ver}:vin:{sha256}`.

    Raises:
        ValueError: Если VIN пустой после нормализации (защита от коллизии
            `sha256("")` для всех пустых VIN).
    """
    normalized = vin.strip().upper()
    if not normalized:
        raise ValueError("VIN is empty after normalization")
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return f"dedup:{_DEDUP_VERSION}:vin:{digest}"


def fuzzy_key(listing: UnifiedListing) -> str:
    """Хэш по make+model+year+mileage (без цены) — когда VIN отсутствует.

    TECHDEBT 3.2: `price` исключён — смена цены («снизил цену, чтобы
    продать») не должна рождать «новое» объявление: это тот же лот.
    `mileage_km` округляется до 1000 км — счётчик пробега гуляет между
    заходами парсера, точное равенство здесь вредно.

    Числовые поля канонизируются (`_norm_int`/`_norm_mileage`), чтобы
    `Decimal("50000")` и `Decimal("50000.0")` давали одинаковый ключ.

    Args:
        listing: Унифицированное объявление.

    Returns:
        Ключ вида `dedup:{ver}:fuzzy:{sha256}`.
    """
    parts = "|".join(
        (
            _norm_str(listing.make),
            _norm_str(listing.model),
            _norm_int(listing.year),
            _norm_mileage(listing.mileage_km),
        )
    )
    digest = hashlib.sha256(parts.encode("utf-8")).hexdigest()
    return f"dedup:{_DEDUP_VERSION}:fuzzy:{digest}"


def _dedup_keys(listing: UnifiedListing) -> list[str]:
    """Собирает непустые dedup-ключи для объявления.

    Пустые/пробельные VIN, make, model игнорируются: `sha256("")` иначе
    схлопнул бы все такие объявления в один «дубль».

    Args:
        listing: Унифицированное объявление.

    Returns:
        Список ключей (0, 1 или 2 элемента).
    """
    keys: list[str] = []
    vin = (listing.vin or "").strip()
    if vin:
        keys.append(vin_key(vin))
    make = (listing.make or "").strip()
    model = (listing.model or "").strip()
    if make and model:
        keys.append(fuzzy_key(listing))
    return keys


async def claim(redis: Redis, listing: UnifiedListing) -> bool:
    """Атомарно «забирает» объявление: `True` — новое, `False` — дубль.

    При `True` все ключи выставлены с TTL; при `False` состояние Redis
    не менялось. Если после `claim` вставка в БД упала — вызывающий
    **обязан** вызвать `release(redis, listing)`, иначе объявление
    потеряется до истечения TTL.

    Args:
        redis: Клиент Redis.
        listing: Унифицированное объявление.

    Returns:
        `True`, если объявление новое (ключи выставлены); `False`, если
        хотя бы один ключ уже существовал (состояние не изменено).
    """
    keys = _dedup_keys(listing)
    if not keys:
        return True  # нечего дедуплицировать — считаем новым
    result = await redis.eval(_CLAIM_SCRIPT, len(keys), *keys, DEDUP_TTL_SEC)
    claimed = bool(result)
    if not claimed:
        logger.debug("dedup rejected listing source=%s id=%s", listing.source, listing.source_id)
    return claimed


async def release(redis: Redis, listing: UnifiedListing) -> None:
    """Откатывает `claim` (best-effort): удаляет ключи объявления.

    Вызывать при любом исключении после успешного `claim`, но до
    подтверждённой вставки в БД (commit/refresh).

    Args:
        redis: Клиент Redis.
        listing: Унифицированное объявление.
    """
    keys = _dedup_keys(listing)
    if not keys:
        return
    try:
        await redis.eval(_RELEASE_SCRIPT, len(keys), *keys)
    except Exception:  # noqa: BLE001 — release не должен маскировать исходную ошибку
        logger.exception(
            "dedup release failed source=%s id=%s", listing.source, listing.source_id
        )


async def is_duplicate(redis: Redis, listing: UnifiedListing) -> bool:
    """True, если объявление уже встречалось за TTL.

    Обёртка над `claim` для обратной совместимости. **Побочный эффект
    сохранён**: при `False` ключи выставлены. Вызывающий код должен
    обрабатывать откат через `release` при ошибке вставки в БД.

    Args:
        redis: Клиент Redis.
        listing: Унифицированное объявление.

    Returns:
        `True` — дубль, `False` — новое (ключи выставлены).
    """
    return not await claim(redis, listing)


def raw_payload(listing: UnifiedListing) -> dict[str, Any]:
    """Сырые данные объявления для S3-объекта (перепарсинг; PLAN.md, фаза 6).

    Раньше писалcя в колонку listings.raw_payload (миграция 0012 выпилила её):
    теперь кладётся в MinIO ключом raw/{source}/{yyyy}/{mm}/{id}_{hash}.json.

    `dedup_keys` помечены `_internal` — формат служебный, не для внешних
    потребителей; SHA-256 от VIN не должен утекать в публичные артефакты.

    Args:
        listing: Унифицированное объявление.

    Returns:
        Словарь для сериализации в S3.
    """
    return {
        "source": listing.source,
        "schema_version": listing.schema_version,
        "raw": listing.raw,
        "_internal": {"dedup_keys": _dedup_keys(listing)},
    }
