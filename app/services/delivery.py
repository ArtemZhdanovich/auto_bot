"""Калькулятор доставки авто Корея → РФ. Ставки — из действующих НПА:

• Пошлины: единые ставки ЕТТ ЕАЭС для физлиц («Автотранспортные средства...»,
  Решение Совета ЕЭК № 54 от 20.07.2018; ранее — Решение КТС № 130).
• Сбор за таможенные операции: ПП РФ № 342 от 26.03.2020 (действующая редакция).
• Утильсбор: ПП РФ № 1291 от 26.12.2013 в ред. ПП № 1257 от 13.09.2024
  (с 01.10.2024): льготные коэффициенты 0.17 (<3 лет) и 0.26 (3+ лет) от
  базовой ставки 20 000 ₽; льгота — личное пользование, не более 1 авто
  в год, объём до 3 000 см³ (иначе полная ставка).

При смене законодательства правятся только константы ниже + тесты.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime

# --- Правовые основания (выводим в UI — расчёт не «из воздуха») -------------
LEGAL_REFS: list[dict[str, str]] = [
    {
        "label": "Пошлины",
        "ref": "ЕТТ ЕАЭС, единые ставки для физлиц — Решение Совета ЕЭК № 54 от 20.07.2018",
    },
    {
        "label": "Сбор за таможенные операции",
        "ref": "Постановление Правительства РФ № 342 от 26.03.2020",
    },
    {
        "label": "Утильсбор",
        "ref": (
            "Постановление Правительства РФ № 1291 от 26.12.2013 (ред. ПП № 1257 "
            "от 13.09.2024): льготные коэффициенты 0.17 и 0.26 от базы 20 000 ₽"
        ),
    },
    {
        "label": "Условия льготы",
        "ref": (
            "Личное пользование, не более 1 авто в год, объём до 3 000 см³ — "
            "иначе полная ставка утильсбора"
        ),
    },
]


# --- Курсовые константы (заглушка MVP; потом — ежедневный курс из ЦБ) ------
USD_RUB = 92.0
EUR_USD = 1.08

# --- Логистика и услуги (USD) ----------------------------------------------
LOGISTICS_USD = 1_800  # море Бусан → Владивосток + портовые и СВХ
BROKER_USD = 250  # услуги брокера во Владивостоке
LAB_USD = 350  # СБКТС + ЭПТС (лаборатория, оформление ЭПТС)

# --- Утильсбор: ПП № 1291 в ред. ПП № 1257 (с 01.10.2024) -------------------
UTIL_BASE_RUB = 20_000  # базовая ставка
UTIL_LTC_UNDER_3Y = 0.17  # льготный коэффициент, авто до 3 лет
UTIL_LTC_OVER_3Y = 0.26  # льготный коэффициент, авто 3 лет и старше
UTIL_RUB_UNDER_3Y = int(UTIL_BASE_RUB * UTIL_LTC_UNDER_3Y)  # 3 400 ₽
UTIL_RUB_OVER_3Y = int(UTIL_BASE_RUB * UTIL_LTC_OVER_3Y)  # 5 200 ₽
# Полные ставки (объём >3 000 см³ — льгота не действует), коэф. 34.43/54.84:
UTIL_FULL_UNDER_3Y = 688_600
UTIL_FULL_OVER_3Y = 1_096_800


def _duty_under_3y(price_usd: float, cc: int) -> float:
    """Пошлина < 3 лет: % от стоимости, но не менее €/см³ (ЕТТ ЕАЭС)."""
    brackets = [  # (предел см³, %, минимум €/см³)
        (1_000, 0.54, 2.5),
        (1_500, 0.54, 2.5),
        (1_800, 0.54, 2.5),
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
_DUTY_OVER_5Y = [  # (предел см³, €/см³)
    (1_000, 3.0), (1_500, 3.2), (1_800, 3.5), (2_300, 4.8), (3_000, 5.0), (10**9, 5.7),
]


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
    legal_refs: list[dict[str, str]] = field(default_factory=lambda: LEGAL_REFS)
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
        util = float(UTIL_FULL_UNDER_3Y if age < 3 else UTIL_FULL_OVER_3Y)
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
