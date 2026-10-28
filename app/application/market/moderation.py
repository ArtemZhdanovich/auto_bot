"""Авто-модерация лотов рынка — Machine Review (редизайн часть 10, §2).

Два слоя:
- `screen_lot` — чистая функция правил: контакты в описании (regex из
  общего доменного модуля `app.domain.shared.contacts`), запрещённые
  слова, выброс цены, короткое описание, новичок, дубликат. Считает
  risk_score и выносит вердикт accept/manual/reject по порогам;
- `ScreenMarketLot` — сценарий сбора фактов через репозиторий (новичок,
  средняя цена модели, дубликат) и вызов чистой оценки. Партнёрские лоты
  правилами не проверяются (слоты платные → авто-accept), но 10% уходит
  в ручную очередь сэмплингом (контроль качества, §2).

LLM не используется: правила дешёвые и предсказуемые (scope-решение §0).
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass
from decimal import Decimal

from app.application.market.dto import MarketLotSnapshot
from app.application.market.ports import MarketLotRepository
from app.config import Settings
from app.domain.shared.contacts import find_contacts

logger = logging.getLogger(__name__)

# Действия вердикта (10 §2).
ACTION_ACCEPT = "accept"
ACTION_MANUAL = "manual"
ACTION_REJECT = "reject"

# Пороги вердикта (10 §2): hard-правило или risk>=80 — reject;
# risk>=30 / new_user / price_outlier — ручная очередь; иначе accept.
REJECT_RISK_THRESHOLD = 80
MANUAL_RISK_THRESHOLD = 30

# Риски правил (каждое суммируется в risk_score).
RISK_CONTACTS = 30
RISK_FORBIDDEN = 40
RISK_PRICE_OUTLIER = 20
RISK_SHORT_DESCRIPTION = 10
RISK_NEW_USER = 25
RISK_DUPLICATE = 35

# short_description: короче N символов — предупреждение (+10).
MIN_DESCRIPTION_LENGTH = 30
# price_outlier: цена < 10% средней по той же модели (comps упрощённо).
PRICE_OUTLIER_FRACTION = Decimal("0.1")
# duplicate: окно повтора owner+make+model+year+price (дней).
DUPLICATE_WINDOW_DAYS = 7
# Сэмплинг партнёрских лотов в ручную очередь (контроль качества, §2).
PARTNER_SAMPLING_RATE = 0.1

# Правила-нарушения (hard → авто-reject) и правила-стопы (soft, но
# форсируют ручную очередь независимо от порога риска).
_HARD_RULES = frozenset({"contacts_in_text", "forbidden_words"})
_MANUAL_RULES = frozenset({"price_outlier", "new_user"})


@dataclass(frozen=True, slots=True)
class ModerationViolation:
    """Сработавшее правило: имя, деталь для владельца/админа, риск."""

    rule: str
    detail: str
    risk: int

    def as_payload(self) -> dict:
        """Сериализация в moderation_payload (JSONB)."""
        return {"rule": self.rule, "detail": self.detail, "risk": self.risk}


@dataclass(frozen=True, slots=True)
class ModerationVerdict:
    """Вердикт скрининга: risk_score, нарушения, действие (10 §2)."""

    risk_score: int
    violations: tuple[ModerationViolation, ...] = ()
    action: str = ACTION_ACCEPT

    @property
    def hard(self) -> bool:
        """Есть hard-нарушение (контакты/запрещённые слова)."""
        return any(v.rule in _HARD_RULES for v in self.violations)

    def payload(self) -> dict:
        """Снимок вердикта для `listings.moderation_payload` (миграция 0030)."""
        return {
            "risk_score": self.risk_score,
            "violations": [v.as_payload() for v in self.violations],
        }

    def rejection_reason(self) -> str:
        """Текст отказа для `rejected_reason` — детали hard-нарушений."""
        hard = [v for v in self.violations if v.rule in _HARD_RULES]
        return "; ".join(f"{v.rule}: {v.detail}" for v in hard)


def screen_lot(
    lot: MarketLotSnapshot,
    *,
    is_new_user: bool = False,
    similar_avg_price: Decimal | None = None,
    has_duplicate: bool = False,
    forbidden_words: tuple[str, ...] | list[str] = (),
    bot_username: str = "",
) -> ModerationVerdict:
    """Прогнать чистые правила скрининга по снимку лота (10 §2).

    Правила и риски: contacts_in_text (+30, hard), forbidden_words в
    описании/марке/модели (+40, hard), price_outlier <10% средней той же
    модели (+20, manual), short_description <30 симв (+10), new_user
    (+25, manual), duplicate за 7 дней (+35).

    Args:
        lot: Снимок лота (описание, марка/модель, цена).
        is_new_user: У владельца нет ни одного accepted-лота кроме этого.
        similar_avg_price: Средняя цена той же модели (год ±2) или None.
        has_duplicate: Тот же owner+make+model+year+price за 7 дней.
        forbidden_words: Словарь из конфига `market_forbidden_words`.
        bot_username: Наш бот (его t.me-ссылка — не контакт).

    Returns:
        Вердикт: risk_score, нарушения, action accept|manual|reject.
    """
    violations: list[ModerationViolation] = []

    description = (lot.owner_description or "").strip()
    contacts = find_contacts(lot.owner_description or "", bot_username)
    if contacts:
        first = contacts[0][:40]
        violations.append(
            ModerationViolation(
                rule="contacts_in_text",
                detail=f"контакт в описании: {first}",
                risk=RISK_CONTACTS,
            )
        )

    # Запрещённые слова ищем по описанию И марке/модели (10 §2).
    haystack = " ".join(
        part for part in (lot.owner_description, lot.make, lot.model) if part
    ).lower()
    matched = [
        word for word in forbidden_words if word.lower() in haystack
    ]
    if matched:
        violations.append(
            ModerationViolation(
                rule="forbidden_words",
                detail=f"запрещённые слова: {', '.join(matched[:3])}",
                risk=RISK_FORBIDDEN,
            )
        )

    if (
        similar_avg_price is not None
        and similar_avg_price > 0
        and lot.price is not None
        and lot.price > 0
        and lot.price < PRICE_OUTLIER_FRACTION * similar_avg_price
    ):
        violations.append(
            ModerationViolation(
                rule="price_outlier",
                detail=(
                    f"цена {lot.price} сильно ниже средней {similar_avg_price} "
                    "по той же модели"
                ),
                risk=RISK_PRICE_OUTLIER,
            )
        )

    if len(description) < MIN_DESCRIPTION_LENGTH:
        violations.append(
            ModerationViolation(
                rule="short_description",
                detail=f"описание короче {MIN_DESCRIPTION_LENGTH} символов",
                risk=RISK_SHORT_DESCRIPTION,
            )
        )

    if is_new_user:
        violations.append(
            ModerationViolation(
                rule="new_user",
                detail="первое объявление владельца",
                risk=RISK_NEW_USER,
            )
        )

    if has_duplicate:
        violations.append(
            ModerationViolation(
                rule="duplicate",
                detail=(
                    f"такой же лот уже публиковался за последние "
                    f"{DUPLICATE_WINDOW_DAYS} дней"
                ),
                risk=RISK_DUPLICATE,
            )
        )

    risk = sum(v.risk for v in violations)
    rules = {v.rule for v in violations}
    if rules & _HARD_RULES or risk >= REJECT_RISK_THRESHOLD:
        action = ACTION_REJECT
    elif rules & _MANUAL_RULES or risk >= MANUAL_RISK_THRESHOLD:
        action = ACTION_MANUAL
    else:
        action = ACTION_ACCEPT
    return ModerationVerdict(
        risk_score=risk, violations=tuple(violations), action=action
    )


class ScreenMarketLot:
    """Сценарий скрининга: факты из БД → чистая оценка `screen_lot`.

    Партнёрские лоты (owner_type='partner') правилами не проверяются —
    слоты платные, авто-accept; 10% выборки уходит в ручную очередь
    (`random < PARTNER_SAMPLING_RATE`, §2).
    """

    def __init__(self, lots: MarketLotRepository, settings: Settings) -> None:
        self._lots = lots
        self._settings = settings

    async def execute(self, lot: MarketLotSnapshot) -> ModerationVerdict:
        """Собрать факты по лоту и вынести вердикт (быстро: 3 точечных
        запроса, синхронно внутри submit — без фоновой очереди, §4).

        Args:
            lot: Снимок лота (после создания/правки).

        Returns:
            Вердикт `ModerationVerdict`.
        """
        if lot.owner_type == "partner":
            # Партнёр: контент не экранится, только выборочный контроль.
            if random.random() < PARTNER_SAMPLING_RATE:
                logger.info(
                    "market_lot_partner_sampled listing_id=%s", lot.listing_id
                )
                return ModerationVerdict(
                    risk_score=0,
                    violations=(
                        ModerationViolation(
                            rule="sampling",
                            detail="выборочный контроль качества партнёрского лота",
                            risk=0,
                        ),
                    ),
                    action=ACTION_MANUAL,
                )
            return ModerationVerdict(risk_score=0, action=ACTION_ACCEPT)

        is_new_user = not await self._lots.has_accepted_user_lots(
            lot.owner_user_id or 0, exclude_listing_id=lot.listing_id
        )
        similar_count, similar_avg = await self._lots.model_price_stats(
            lot.make or "",
            lot.model or "",
            lot.year,
            lot.currency,
            exclude_listing_id=lot.listing_id,
        )
        has_duplicate = await self._lots.has_duplicate_lot(
            lot.owner_user_id or 0,
            lot.make or "",
            lot.model or "",
            lot.year,
            lot.price or Decimal(0),
            exclude_listing_id=lot.listing_id,
            days=DUPLICATE_WINDOW_DAYS,
        )
        verdict = screen_lot(
            lot,
            is_new_user=is_new_user,
            # Нет сравниваемых лотов (count=0) — правило не срабатывает.
            similar_avg_price=similar_avg if similar_count > 0 else None,
            has_duplicate=has_duplicate,
            forbidden_words=self._settings.market_forbidden_words,
            bot_username=self._settings.bot_username,
        )
        logger.info(
            "market_lot_screened listing_id=%s action=%s risk=%s",
            lot.listing_id,
            verdict.action,
            verdict.risk_score,
        )
        return verdict
