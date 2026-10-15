"""Калькулятор доставки авто Корея → РФ (MVP, п. «менеджер»).

Расчёт для физического лица, личное пользование (льготный утильсбор).
Ставки — константы модуля с комментарием источника; при смене
законодательства правятся только они.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime

# --- Курсовые константы (заглушка MVP; потом — ежедневный курс из ЦБ) ------
USD_RUB = 92.0
EUR_USD = 1.08

# --- Логистика и услуги (USD) ----------------------------------------------
LOGISTICS_USD = 1_800  # море Бусан → Владивосток + портовые и СВХ
BROKER_USD = 250  # услуги брокера во Владивостоке
LAB_USD = 350  # СБКТС + ЭПТС (лаборатория, оформление ЭПТС)

# --- Утильсбор (физлицо, личное пользование; льготный коэффициент) ---------
UTIL_RUB_UNDER_3Y = 3_400
UTIL_RUB_OVER_3Y = 5_200
UTIL_RUB_FULL_OVER_3000CC = 136_200  # полная ставка (не личное) для >3000 см³


def _duty_under_3y(price_usd: float, cc: int) -> float:
    """Пошлина < 3 лет: % от стоимости, но не менее €/см³ (ЕТТ ЕАЭС)."""
    brackets = [  # (предел см³, %, минимум €/см³)
        (1_000, 0.54, 2.5),
        (1_500, 0.54, 2.5),
        (1_800, 0.50, 2.5),
        (2_300, 0.48, 2.7),
        (3_000, 0.48, 3.0),
        (10**9, 0.48, 3.6),
    ]
    for limit, pct, min_eur_cc in brackets:
        if cc <= limit:
            eur = price_usd * pct / EUR_USD
            return max(eur, min_eur_cc * cc) * EUR_USD
    return 0.0


_DUTY_3_5Y = [  # (предел см³, €/см³)
    (1_000, 1.5), (1_500, 1.7), (1_800, 2.5), (2_300, 2.7), (3_000, 3.0), (10**9, 3.6),
]
_DUTY_OVER_5Y = [(1_000, 3.0), (1_500, 3.2), (1_800, 3.5), (2_300, 4.8), (10**9, 5.0)]


def _duty_by_age(age: int, price_usd: float, cc: int) -> float:
    if age < 3:
        return _duty_under_3y(price_usd, cc)
    table = _DUTY_3_5Y if age <= 5 else _DUTY_OVER_5Y
    for limit, eur_cc in table:
        if cc <= limit:
            return eur_cc * cc * EUR_USD
    return 0.0


def _clearance_fee(customs_value_rub: float) -> float:
    """Сбор за таможенные операции (постановление 342)."""
    brackets = [
        (200_000, 1_067), (450_000, 2_134), (1_200_000, 4_269),
        (2_700_000, 11_746), (4_200_000, 16_524), (5_500_000, 21_344),
        (7_000_000, 25_843), (10**12, 30_000),
    ]
    for limit, fee in brackets:
        if customs_value_rub <= limit:
            return float(fee)
    return 30_000.0


@dataclass
class DeliveryBreakdown:
    """Итог расчёта «под ключ» во Владивостоке."""

    price_usd: float
    engine_cc: int
    year: int
    age_years: int
    age_group: str
    logistics_usd: float
    broker_usd: float
    lab_usd: float
    duty_usd: float
    fee_rub: float
    util_rub: float
    components: list[dict] = field(default_factory=list)
    total_rub: float = 0.0
    total_usd: float = 0.0

    def finalize(self) -> "DeliveryBreakdown":
        car_rub = self.price_usd * USD_RUB
        logistics_rub = (self.logistics_usd + self.broker_usd + self.lab_usd) * USD_RUB
        self.components = [
            {"label": "Авто по счёту", "usd": round(self.price_usd), "rub": round(car_rub)},
            {
                "label": "Доставка море + порт",
                "usd": round(self.logistics_usd),
                "rub": round(self.logistics_usd * USD_RUB),
            },
            {"label": "Брокер во Владивостоке", "usd": self.broker_usd, "rub": round(self.broker_usd * USD_RUB)},
            {"label": "СБКТС + ЭПТС", "usd": self.lab_usd, "rub": round(self.lab_usd * USD_RUB)},
            {
                "label": f"Таможенная пошлина ({self.age_group})",
                "usd": round(self.duty_usd),
                "rub": round(self.duty_usd * USD_RUB),
            },
            {"label": "Таможенный сбор", "usd": round(self.fee_rub / USD_RUB), "rub": round(self.fee_rub)},
            {"label": "Утильсбор (льготный)", "usd": round(self.util_rub / USD_RUB), "rub": round(self.util_rub)},
        ]
        self.total_rub = car_rub + logistics_rub + self.duty_usd * USD_RUB + self.fee_rub + self.util_rub
        self.total_usd = round(self.total_rub / USD_RUB)
        return self


def calc_delivery(
    price_usd: float, engine_cc: int, year: int, *, current_year: int | None = None
) -> DeliveryBreakdown:
    """Полный расчёт стоимости «авто + доставка + таможня» во Владивостоке."""
    cc = max(50, int(engine_cc))
    now = current_year or datetime.now(UTC).year
    age = max(0, now - int(year))
    group = "до 3 лет" if age < 3 else ("3–5 лет" if age <= 5 else "старше 5 лет")

    duty = _duty_by_age(age, price_usd, cc)
    customs_value_rub = price_usd * USD_RUB
    fee = _clearance_fee(customs_value_rub)
    if cc > 3_000:
        util = float(UTIL_RUB_FULL_OVER_3000CC)
    else:
        util = float(UTIL_RUB_UNDER_3Y if age < 3 else UTIL_RUB_OVER_3Y)

    return DeliveryBreakdown(
        price_usd=price_usd,
        engine_cc=cc,
        year=int(year),
        age_years=age,
        age_group=group,
        logistics_usd=LOGISTICS_USD,
        broker_usd=BROKER_USD,
        lab_usd=LAB_USD,
        duty_usd=round(duty),
        fee_rub=fee,
        util_rub=util,
    ).finalize()


def format_breakdown(b: DeliveryBreakdown) -> str:
    """Человекочитаемая сводка для бота (числа с пробелами, не «18,000»)."""
    lines = ["🚚 <b>Расчёт: авто + доставка + таможня</b> (Корея → Владивосток)"]
    lines.append(f"\nАвто: {b.price_usd:,.0f} $ · {b.engine_cc} см³ · {b.year} г. ({b.age_group})\n")
    for c in b.components:
        lines.append(f"• {c['label']}: {c['rub']:,.0f} ₽")
    lines.append(f"\n<b>Итого «под ключ»: ~{b.total_rub:,.0f} ₽ (~{b.total_usd:,.0f} $)</b>")
    lines.append("\n<i>Ставки для физлица (личное пользование), курс фиксированный. "
                 "Точную смету даст менеджер — в приложении кнопка «Связаться».</i>")
    return "\n".join(lines).replace(",", " ")
