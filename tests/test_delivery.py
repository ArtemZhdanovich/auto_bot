"""Калькулятор доставки Корея → РФ (MVP)."""

from app.services.delivery import LEGAL_REFS, calc_delivery, format_breakdown

CURRENT_YEAR = 2026


def test_age_groups():
    b = calc_delivery(18_000, 2_000, 2024, current_year=CURRENT_YEAR)
    assert b.age_group == "до 3 лет"
    b = calc_delivery(18_000, 2_000, 2023, current_year=CURRENT_YEAR)
    assert b.age_group == "3–5 лет"
    b = calc_delivery(18_000, 2_000, 2015, current_year=CURRENT_YEAR)
    assert b.age_group == "старше 5 лет"


def test_duty_3_5y_euro_per_cc():
    # 2.7 €/см³ (1800–2300) × 2000 см³ = 5400 € × 1.08 = 5832 $
    b = calc_delivery(18_000, 2_000, 2023, current_year=CURRENT_YEAR)
    assert b.duty_usd == 5832


def test_duty_under_3y_1500_1800_is_54pct_ett_eaes():
    # ЕТТ ЕАЭС: 1500–1800 см³ до 3 лет = 54%, мин 2.5 €/см³ (было ошибочно 50%)
    b = calc_delivery(40_000, 1_700, 2025, current_year=CURRENT_YEAR)
    # 54% от 40 000$ = 21 600$ → 20 000€ > минимума 2.5×1700=4250€ → берём процент
    assert b.duty_usd == round(40_000 * 0.54)


def test_duty_over_5y_over_3000cc_is_57_eur_cc():
    # ЕТТ ЕАЭС: старше 5 лет, >3000 см³ = 5.7 €/см³ (было ошибочно 5.0)
    b = calc_delivery(30_000, 3_400, 2018, current_year=CURRENT_YEAR)
    assert b.duty_usd == round(5.7 * 3_400 * 1.08)


def test_duty_under_3y_percentage_not_less_than_euro():
    # Дешёвое авто с большим объёмом: побеждает минимум €/см³
    b = calc_delivery(3_000, 2_500, 2025, current_year=CURRENT_YEAR)
    # 48% от 3000$ = 1440$ → 1333€; минимум 3.0 €/cc (2300–3000) × 2500 = 7500€ → 8100$
    assert b.duty_usd == round(7500 * 1.08)
    # Дорогое авто: 48% от 60k$ = 28800$ → побеждает процент
    b2 = calc_delivery(60_000, 2_500, 2025, current_year=CURRENT_YEAR)
    assert b2.duty_usd == round(60_000 * 0.48)


def test_util_fee_rules():
    # Льготный для личного пользования
    b = calc_delivery(18_000, 2_000, 2022, current_year=CURRENT_YEAR)
    assert b.util_rub == 5_200  # 3–5 лет
    b2 = calc_delivery(18_000, 2_000, 2025, current_year=CURRENT_YEAR)
    assert b2.util_rub == 3_400  # до 3 лет
    # >3000 см³ — льгота не действует: полная ставка зависит от возраста
    # (ПП № 1291 в ред. № 1257, с 01.10.2024: 34.43/54.84 × 20 000 ₽)
    b3 = calc_delivery(50_000, 3_300, 2025, current_year=CURRENT_YEAR)
    assert b3.util_rub == 688_600  # до 3 лет
    b4 = calc_delivery(50_000, 3_300, 2022, current_year=CURRENT_YEAR)
    assert b4.util_rub == 1_096_800  # 3+ лет


def test_totals_consistent():
    b = calc_delivery(18_000, 2_000, 2022, current_year=CURRENT_YEAR)
    assert b.total_rub > 0
    assert b.total_usd == round(b.total_rub / 92.0)
    # Смета всегда дороже цены авто
    assert b.total_usd > b.price_usd
    assert len(b.components) == 7


def test_format_breakdown_contains_total():
    text = format_breakdown(calc_delivery(18_000, 2_000, 2022, current_year=CURRENT_YEAR))
    assert "Итого" in text
    assert "2 000 см³" in text or "2000 см³" in text


def test_legal_refs_cite_real_documents():
    """Расчёт обязан ссылаться на действующие НПА, а не «из воздуха»."""
    refs = " ".join(r["ref"] for r in LEGAL_REFS)
    assert "54 от 20.07.2018" in refs        # ЕТТ ЕАЭС (пошлины)
    assert "342" in refs                     # сбор за тамож. операции
    assert "1291" in refs and "1257" in refs # утильсбор + последняя редакция
    assert "0.17" in refs and "0.26" in refs # льготные коэффициенты
    # Разбор выдаёт те же ссылки вместе с расчётом
    b = calc_delivery(18_000, 2_000, 2022, current_year=CURRENT_YEAR)
    assert b.legal_refs == LEGAL_REFS
    assert len(b.legal_refs) == 4
