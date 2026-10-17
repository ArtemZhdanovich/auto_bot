"""Калькулятор доставки авто Корея → РФ. Ставки — из действующих НПА
(сверено 16.10.2026):

• Пошлины: единые ставки ЕТТ ЕАЭС для физлиц (Решение Совета ЕЭК № 54
  от 20.07.2018) — без изменений с 2020 года.
• Сбор за таможенные операции: ПП РФ № 1637 от 28.11.2024 в ред. ПП № 1638
  от 23.10.2025 (ставки действуют с 01.01.2026; верхний порог партии поднят
  до 10 млн ₽).
• Утильсбор: ПП РФ № 1291 от 26.12.2013, действующая редакция с 01.12.2025 —
  базовая ставка 20 000 ₽ (М1), коэффициент — прогрессивная шкала по
  МОЩНОСТИ двигателя (л.с.) в сочетании с объёмом; плановая индексация
  коэффициентов с 01.01.2026 учтена. Следующая индексация — с 01.01.2027
  (+10–20% по Минпромторгу).
  Льгота для физлица (личное пользование, не более 1 авто в год):
  0.17 / 0.26 от базы (3 400 / 5 200 ₽) — только при объёме до 3 000 см³
  И мощности до 160 л.с. включительно. Иначе — полная ставка по шкале.

При смене законодательства правятся только константы ниже + тесты.

Регион (region="ru" | "by"): валюта объявления задаёт юрисдикцию расчёта —
BYN → Беларусь (свои сбор/утильсбор/маршрут, итог в BYN), RUB/USD → РФ
(итог в ₽). Пошлины ЕАЭС (Решение Совета ЕЭК № 54) едины, различаются сбор
за оформление, утильсбор, логистика и СБКТС/ЭПТС. Ставки РБ — ориентир MVP:
в UI и rates_note явно помечены «проверять актуальную редакцию».
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime

Region = str  # "ru" | "by"

# --- Правовые основания РФ ---------------------------------------------------
LEGAL_REFS: list[dict[str, str]] = [
    {
        "label": "Пошлины",
        "ref": "ЕТТ ЕАЭС, единые ставки для физлиц — Решение Совета ЕЭК № 54 от 20.07.2018",
    },
    {
        "label": "Сбор за таможенные операции",
        "ref": "Постановление Правительства РФ № 1637 от 28.11.2024 (ред. № 1638 от 23.10.2025, с 01.01.2026)",
    },
    {
        "label": "Утильсбор",
        "ref": (
            "Постановление Правительства РФ № 1291 от 26.12.2013 (действующая ред. с 01.12.2025): "
            "база 20 000 ₽ × коэффициент по мощности; индексация с 01.01.2026"
        ),
    },
    {
        "label": "Условия льготы",
        "ref": (
            "Личное пользование, не более 1 авто в год, объём до 3 000 см³ и мощность до 160 л.с. — "
            "иначе полная ставка утильсбора"
        ),
    },
    {
        "label": "Актуальность ставок",
        "ref": (
            "Ставки сверены 16.10.2026; очередная индексация утильсбора — с 01.01.2027 (+10–20%). "
            "Электромобили и последовательные гибриды считаются по отдельной таблице ПП № 1291"
        ),
    },
]

# --- Правовые основания РБ (ставки-ориентиры: проверять на customs.gov.by) ---
LEGAL_REFS_BY: list[dict[str, str]] = [
    {
        "label": "Пошлины",
        "ref": "ЕТТ ЕАЭС, единые ставки для физлиц — Решение Совета ЕЭК № 54 от 20.07.2018 (общие для РБ и РФ)",
    },
    {
        "label": "Сбор за таможенное оформление",
        "ref": "Ставки сбора РБ — постановление Совмина РБ (ориентир ~48 BYN); проверять актуальную редакцию",
    },
    {
        "label": "Утильсбор РБ",
        "ref": (
            "Постановление Совмина РБ: базовая ставка 544,5 BYN × коэффициент (объём/возраст); "
            "льготный коэффициент для физлиц ~0.5 при объёме ≤ 3 000 см³; проверять актуальную редакцию"
        ),
    },
    {
        "label": "Маршрут и учёт",
        "ref": "Корея → Владивосток → Минск; СБКТС/ЭПТС и постановка на учёт в ГАИ РБ",
    },
    {
        "label": "Актуальность ставок",
        "ref": (
            "Ставки РБ — ориентир MVP (сверены 16.10.2026). Перед сделкой проверяйте расчёт "
            "на customs.gov.by (калькулятор ГТК Беларуси) или у брокера"
        ),
    },
]


# --- Курсовые константы (заглушка MVP; потом — ежедневный курс из ЦБ/НРБ) ----
USD_RUB = 92.0
USD_BYN = 3.05
EUR_USD = 1.08

# --- Логистика и услуги (USD) ----------------------------------------------
LOGISTICS_USD = 1_800  # море Бусан → Владивосток + портовые и СВХ
BROKER_USD = 250  # услуги брокера во Владивостоке
LAB_USD = 350  # СБКТС + ЭПТС (лаборатория, оформление ЭПТС)

# РБ: море до Владивостока + перегон/автовоз Владивосток → Минск (ориентир).
LOGISTICS_BY_USD = 3_600
BROKER_BY_USD = 150  # брокер/декларант в РБ
LAB_BY_USD = 250  # СБКТС + ЭПТС в РБ

# --- Сбор за таможенные операции: ПП № 1637 (ред. № 1638), с 01.01.2026 -----
# (верхняя граница таможенной стоимости ₽ → сбор ₽); порог партии — 10 млн ₽.
CLEARANCE_FEE_BRACKETS: list[tuple[int, int]] = [
    (200_000, 1_231),
    (450_000, 2_462),
    (1_200_000, 4_924),
    (2_700_000, 13_541),
    (4_200_000, 18_465),
    (5_500_000, 21_344),
    (10_000_000, 49_240),
    (10**12, 73_860),
]

# --- Утильсбор: ПП № 1291 (ред. с 01.12.2025 + индексация 01.01.2026) -------
UTIL_BASE_RUB = 20_000  # базовая ставка (М1)
UTIL_LTC_UNDER_3Y = 0.17  # льготный коэффициент, авто до 3 лет
UTIL_LTC_OVER_3Y = 0.26  # льготный коэффициент, авто 3 лет и старше
UTIL_RUB_UNDER_3Y = int(UTIL_BASE_RUB * UTIL_LTC_UNDER_3Y)  # 3 400 ₽
UTIL_RUB_OVER_3Y = int(UTIL_BASE_RUB * UTIL_LTC_OVER_3Y)  # 5 200 ₽
UTIL_HP_LIMIT = 160  # льгота действует только при мощности ≤ 160 л.с.
UTIL_CC_LIMIT = 3_000  # …и объёме ≤ 3 000 см³

# Полные коэффициенты (шкала «объём × мощность», индексация 01.01.2026):
# (верхняя граница диапазона л.с., коэф. до 3 лет, коэф. 3+ лет).
# Диапазоны соответствуют строкам таблицы ПП № 1291: 160–190, 190–220, …
# (для 1,0–2,0 л при ≤160 л.с. действует льготный, поэтому старт от 190).
_UTIL_SCALE_LE_2000: list[tuple[int, float, float]] = [
    (190, 45.00, 76.64), (220, 47.64, 79.20), (250, 50.52, 83.88),
    (280, 57.12, 91.92), (310, 64.56, 100.56), (340, 72.96, 110.16),
    (370, 83.16, 120.60), (400, 94.80, 132.00), (430, 108.00, 144.60),
    (460, 123.24, 158.40), (500, 140.40, 173.40), (10**9, 160.08, 189.84),
]
_UTIL_SCALE_2000_3000: list[tuple[int, float, float]] = [
    (190, 115.34, 172.80), (220, 118.20, 175.08), (250, 120.12, 177.60),
    (280, 126.00, 183.00), (310, 131.04, 188.52), (340, 136.32, 193.68),
    (370, 141.72, 199.08), (400, 147.48, 204.72), (430, 153.36, 210.48),
    (460, 159.48, 216.36), (500, 165.84, 222.36), (10**9, 172.44, 228.60),
]
_UTIL_SCALE_3000_3500: list[tuple[int, float, float]] = [
    (160, 129.20, 197.81), (190, 131.76, 200.04), (220, 134.40, 202.20), (250, 137.16, 204.36),
    (280, 140.52, 207.24), (310, 144.00, 212.40), (340, 151.92, 217.80),
    (370, 160.32, 224.28), (400, 169.20, 231.00), (430, 178.44, 237.96),
    (460, 188.28, 245.04), (500, 198.60, 252.48), (10**9, 209.52, 260.04),
]
_UTIL_SCALE_GT_3500: list[tuple[int, float, float]] = [
    (160, 164.53, 216.29), (190, 167.28, 219.48), (220, 170.16, 222.84), (250, 173.04, 226.20),
    (280, 176.52, 231.36), (310, 180.00, 236.64), (340, 186.36, 249.60),
    (370, 192.88, 263.40), (400, 199.68, 277.92), (430, 206.64, 293.16),
    (460, 213.84, 309.36), (500, 221.28, 326.40), (10**9, 229.08, 344.28),
]


def _util_full_scale(cc: int) -> list[tuple[int, float, float]]:
    """Шкала полных коэффициентов для объёма двигателя (см³)."""
    if cc <= 2_000:
        return _UTIL_SCALE_LE_2000
    if cc <= 3_000:
        return _UTIL_SCALE_2000_3000
    if cc <= 3_500:
        return _UTIL_SCALE_3000_3500
    return _UTIL_SCALE_GT_3500


# --- Сбор за таможенное оформление РБ (ориентир; проверять акту. редакцию) ---
CLEARANCE_FEE_BYN = 48.0

# --- Утильсбор РБ (ориентир; проверять актуальную редакцию) ------------------
UTIL_BY_BASE_BYN = 544.5  # базовая ставка
UTIL_BY_LTC_COEF = 0.5  # льготный коэффициент физлица (объём ≤ 3 000 см³)
UTIL_BY_CC_LIMIT = 3_000
UTIL_BY_LTC_BYN = UTIL_BY_BASE_BYN * UTIL_BY_LTC_COEF  # ≈ 272 BYN
UTIL_BY_FULL_COEF = 2.0  # ориентир полной ставки вне льготы (объём/возраст)
UTIL_BY_FULL_BYN = UTIL_BY_BASE_BYN * UTIL_BY_FULL_COEF

UTIL_BY_NOTE = (
    "Утильсбор РБ — ориентир: базовая ставка 544,5 BYN × коэффициент "
    "(льготный ~0.5 для физлиц при объёме до 3 000 см³). Проверяйте "
    "актуальную редакцию постановления Совмина РБ на дату ввоза."
)


def _util_fee_by(cc: int) -> tuple[float, str]:
    """Утильсбор РБ → (сумма BYN, категория). Льгота физлица: объём ≤ 3 000 см³."""
    if cc <= UTIL_BY_CC_LIMIT:
        return UTIL_BY_LTC_BYN, "льготный"
    return UTIL_BY_FULL_BYN, "полный"


def _util_fee(cc: int, age: int, power_hp: int | None) -> tuple[float, str]:
    """Утильсбор → (сумма ₽, категория «льготный»/«полный»).

    Льгота — личное пользование, ≤1 авто/год, объём ≤3 000 см³ и мощность
    ≤160 л.с. включительно. Если мощность не указана, льгота предполагается
    (с оговоркой в util_note расчёта).
    """
    lt3 = age < 3
    over_hp = power_hp is not None and power_hp > UTIL_HP_LIMIT
    if not over_hp and cc <= UTIL_CC_LIMIT:
        return float(UTIL_RUB_UNDER_3Y if lt3 else UTIL_RUB_OVER_3Y), "льготный"
    hp = power_hp or UTIL_HP_LIMIT
    for hp_limit, coef_lt3, coef_3plus in _util_full_scale(cc):
        if hp <= hp_limit:
            return UTIL_BASE_RUB * (coef_lt3 if lt3 else coef_3plus), "полный"
    return UTIL_BASE_RUB * 300.0, "полный"  # недостижимо: последняя ступень ∞


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
    """Сбор за таможенные операции (ПП № 1637 в ред. № 1638, с 01.01.2026)."""
    for limit, fee in CLEARANCE_FEE_BRACKETS:
        if customs_value_rub <= limit:
            return float(fee)
    return float(CLEARANCE_FEE_BRACKETS[-1][1])


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
    fee_rub: float  # сбор в местной валюте (₽ для РФ, BYN для РБ)
    util_rub: float  # утильсбор в местной валюте
    power_hp: int | None = None
    region: str = "ru"
    util_category: str = "льготный"
    util_note: str = ""
    components: list[dict] = field(default_factory=list)
    legal_refs: list[dict[str, str]] = field(default_factory=lambda: LEGAL_REFS)
    total_rub: float = 0.0  # итог в местной валюте (совместимость названия)
    total_usd: float = 0.0
    total_local: float = 0.0
    currency: str = "₽"

    def finalize(self) -> "DeliveryBreakdown":
        by = self.region == "by"
        rate = USD_BYN if by else USD_RUB
        self.currency = "Br" if by else "₽"
        car_local = self.price_usd * rate
        util_label = (f"Утильсбор РБ ({self.util_category})" if by else f"Утильсбор ({self.util_category})")
        self.components = [
            {"label": "Авто по счёту", "usd": round(self.price_usd), "rub": round(car_local), "local": round(car_local)},
            {
                "label": "Доставка море + порт" + (" + перегон до Минска" if by else ""),
                "usd": round(self.logistics_usd),
                "rub": round(self.logistics_usd * rate),
                "local": round(self.logistics_usd * rate),
            },
            {
                "label": "Брокер" + (" в Минске" if by else " во Владивостоке"),
                "usd": self.broker_usd,
                "rub": round(self.broker_usd * rate),
                "local": round(self.broker_usd * rate),
            },
            {
                "label": "СБКТС + ЭПТС" + (" (РБ)" if by else ""),
                "usd": self.lab_usd,
                "rub": round(self.lab_usd * rate),
                "local": round(self.lab_usd * rate),
            },
            {
                "label": f"Таможенная пошлина ({self.age_group})",
                "usd": round(self.duty_usd),
                "rub": round(self.duty_usd * rate),
                "local": round(self.duty_usd * rate),
            },
            {
                "label": "Сбор за таможенное оформление" if by else "Таможенный сбор",
                "usd": round(self.fee_rub / rate),
                "rub": round(self.fee_rub),
                "local": round(self.fee_rub),
            },
            {"label": util_label, "usd": round(self.util_rub / rate), "rub": round(self.util_rub), "local": round(self.util_rub)},
        ]
        self.total_local = round(
            car_local
            + (self.logistics_usd + self.broker_usd + self.lab_usd) * rate
            + self.duty_usd * rate
            + self.fee_rub
            + self.util_rub
        )
        self.total_rub = self.total_local  # совместимость: для РФ это ₽
        self.total_usd = round(self.total_local / rate)
        return self


def calc_delivery(
    price_usd: float,
    engine_cc: int,
    year: int,
    *,
    region: str = "ru",
    power_hp: int | None = None,
    current_year: int | None = None,
) -> DeliveryBreakdown:
    """Расчёт «авто + доставка + таможня»: region="ru" → Владивосток (₽), "by" → Минск (BYN).

    Юрисдикцию задаёт валюта объявления на фронте: BYN → РБ, RUB → РФ.
    """
    cc = max(50, int(engine_cc))
    hp = int(power_hp) if power_hp else None
    now = current_year or datetime.now(UTC).year
    age = max(0, now - int(year))
    group = "до 3 лет" if age < 3 else ("3–5 лет" if age <= 5 else "старше 5 лет")

    duty = _duty_by_age(age, price_usd, cc)
    if region == "by":
        fee = CLEARANCE_FEE_BYN
        util, util_category = _util_fee_by(cc)
        util_note = UTIL_BY_NOTE
    else:
        customs_value_rub = price_usd * USD_RUB
        fee = _clearance_fee(customs_value_rub)
        util, util_category = _util_fee(cc, age, hp)
        util_note = ""
        if util_category == "льготный" and hp is None:
            util_note = (
                "Мощность не указана: льготный утильсбор показан в предположении "
                f"до {UTIL_HP_LIMIT} л.с. При мощности выше льгота не действует."
            )

    return DeliveryBreakdown(
        price_usd=price_usd,
        engine_cc=cc,
        year=int(year),
        age_years=age,
        age_group=group,
        logistics_usd=LOGISTICS_BY_USD if region == "by" else LOGISTICS_USD,
        broker_usd=BROKER_BY_USD if region == "by" else BROKER_USD,
        lab_usd=LAB_BY_USD if region == "by" else LAB_USD,
        duty_usd=round(duty),
        fee_rub=fee,
        util_rub=util,
        power_hp=hp,
        region="by" if region == "by" else "ru",
        util_category=util_category,
        util_note=util_note,
        legal_refs=LEGAL_REFS_BY if region == "by" else LEGAL_REFS,
    ).finalize()


def format_breakdown(b: DeliveryBreakdown) -> str:
    """Человекочитаемая сводка для бота (числа с пробелами, не «18,000»)."""
    hp = f" · {b.power_hp} л.с." if b.power_hp else ""
    lines = ["🚚 <b>Расчёт: авто + доставка + таможня</b> (Корея → Владивосток)"]
    lines.append(f"\nАвто: {b.price_usd:,.0f} $ · {b.engine_cc} см³{hp} · {b.year} г. ({b.age_group})\n")
    for c in b.components:
        lines.append(f"• {c['label']}: {c['rub']:,.0f} ₽")
    lines.append(f"\n<b>Итого «под ключ»: ~{b.total_rub:,.0f} ₽ (~{b.total_usd:,.0f} $)</b>")
    lines.append(
        "\n<i>Ставки для физлица (личное пользование, не более 1 авто в год). "
        "Льготный утильсбор — только при мощности до 160 л.с. и объёме до 3000 см³. "
        "Ставки сверены 16.10.2026, курс фиксированный. "
        "Точную смету даст менеджер — в приложении кнопка «Связаться».</i>"
    )
    if b.util_note:
        lines.append(f"\n⚠️ {b.util_note}")
    return "\n".join(lines).replace(",", " ")
