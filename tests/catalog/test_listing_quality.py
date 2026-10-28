"""«Качество объявления» (12 §2): чистая функция без LLM — правила/границы."""

from __future__ import annotations

from decimal import Decimal

from app.application.market.quality import (
    DESCRIPTION_MIN_LEN,
    PHOTOS_FEW_THRESHOLD,
    listing_quality,
)


def test_perfect_listing_scores_100():
    quality = listing_quality(
        photos_count=5,
        description_len=200,
        price=Decimal("30000"),
        currency="USD",
        comps_median=30000,
        make="Kia",
        model="Carnival",
        year=2021,
    )
    assert quality == {"score": 100, "tips": []}


def test_no_photos_penalty_30():
    quality = listing_quality(photos_count=0, description_len=200)
    assert quality["score"] == 70
    assert any("фото" in tip for tip in quality["tips"])


def test_few_photos_penalty_25_with_tip():
    quality = listing_quality(photos_count=2, description_len=200)
    assert quality["score"] == 75
    assert any("кузова и салона" in tip for tip in quality["tips"])


def test_short_description_penalty_20():
    quality = listing_quality(photos_count=5, description_len=40)
    assert quality["score"] == 80
    assert any("описание" in tip.lower() for tip in quality["tips"])


def test_price_above_market_penalty_15():
    quality = listing_quality(
        photos_count=5,
        description_len=200,
        price=40000,
        currency="USD",
        comps_median=30000,
    )
    assert quality["score"] == 85
    assert any("Цена выше рынка" in tip for tip in quality["tips"])


def test_price_below_market_penalty_15():
    quality = listing_quality(
        photos_count=5,
        description_len=200,
        price=20000,
        currency="USD",
        comps_median=30000,
    )
    assert quality["score"] == 85
    assert any("Цена ниже рынка" in tip for tip in quality["tips"])


def test_no_market_median_no_price_penalty():
    """Без медианы правило цены молчит (рынка нет — не паникуем)."""
    quality = listing_quality(
        photos_count=5, description_len=200, price=999999
    )
    assert quality["score"] == 100


def test_boundaries():
    """Границы правил: ровно 3 фото / 80 знаков / +20% — без штрафа."""
    at_edges = listing_quality(
        photos_count=PHOTOS_FEW_THRESHOLD,
        description_len=DESCRIPTION_MIN_LEN,
        price=12000,
        comps_median=10000,
    )
    assert at_edges == {"score": 100, "tips": []}

    just_over = listing_quality(
        photos_count=PHOTOS_FEW_THRESHOLD,
        description_len=DESCRIPTION_MIN_LEN,
        price=12001,
        comps_median=10000,
    )
    assert just_over["score"] == 85  # только штраф цены


def test_all_penalties_stacked():
    """Все три штрафа сразу: 100 − 30 − 20 − 15 = 35 и три совета."""
    quality = listing_quality(
        photos_count=0, description_len=0, price=1, comps_median=1000
    )
    assert quality["score"] == 35
    assert len(quality["tips"]) == 3
