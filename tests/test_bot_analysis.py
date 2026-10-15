"""🤖 AI-анализ цены в боте: кнопка карточки, формат разбора, квота/ошибки."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import app.bot.handlers as h
from app.bot.handlers import (
    _car_card_kb,
    _format_analysis,
)


class FakeMsg:
    def __init__(self):
        self.answers = []
        self.edits = []

    async def answer(self, text, reply_markup=None):
        self.answers.append((text, reply_markup))
        return self

    async def edit_text(self, text, reply_markup=None):
        self.edits.append((text, reply_markup))


class FakeCb:
    def __init__(self, data):
        self.data = data
        self.from_user = SimpleNamespace(id=777)
        self.message = FakeMsg()
        self.answered = False

    async def answer(self, *a, **kw):
        self.answered = True


# ---------- _format_analysis: текст разбора ----------


def _analysis() -> dict:
    return {
        "verdict": "deal",
        "price": 30000.0,
        "currency": "USD",
        "fair_min": 29000,
        "fair_max": 33000,
        "summary": "Цена ниже рынка",
        "factors": ["медиана 31000", "пробег средний"],
        "comps": {"count": 12, "min": 28000, "max": 35000, "median": 31000},
        "history": [],
        "source": "ai",
    }


def test_format_analysis_verdict_range_delta():
    text = _format_analysis(_analysis(), {"make": "Kia", "model": "Carnival"})
    assert "AI-анализ стоимости" in text
    assert "Kia Carnival" in text
    assert "🟢" in text and "Выгодная" in text
    assert "29 000–33 000 USD" in text
    # (30000-31000)/31000 = -3%
    assert "−3%" in text
    assert "Цена ниже рынка" in text
    assert "1 разбор машины в неделю" in text  # напоминание про монетизацию


def test_format_analysis_title_with_year_and_money_lines():
    text = _format_analysis(
        _analysis(), {"make": "Kia", "model": "Carnival", "year": 2021}
    )
    assert "Kia Carnival, 2021" in text
    assert "💵 Цена: 30 000 USD" in text
    assert "📊 Рынок: 12 сопоставимых · медиана 31 000 USD" in text
    assert "📉" in text  # цена ниже середины


def test_format_analysis_overpriced_and_without_comps():
    a = _analysis()
    a.update(verdict="overpriced", comps=None, fair_min=None, fair_max=None, summary=None)
    text = _format_analysis(a, None)
    assert "🔴" in text and "Дорого" in text
    assert "🟢" not in text


def test_car_card_kb_has_analyze_button():
    kb = _car_card_kb({"id": 5, "url": "https://encar.com/x"})
    flat = [b for row in kb.inline_keyboard for b in row]
    analyze = [b for b in flat if b.callback_data == "analyze:5"]
    assert analyze and analyze[0].text == "🤖 Анализ цены"


# ---------- analyze_from_card: хендлер ----------


def _cb():
    return FakeCb("analyze:42")


def test_analyze_ok_sends_verdict_and_miniapp(monkeypatch):
    monkeypatch.setattr(h, "_miniapp_url", lambda: "https://x:8443")
    mock = AsyncMock(return_value=_analysis())
    monkeypatch.setattr(h.backend, "analyze_price", mock)
    monkeypatch.setattr(h.backend, "get_listing", AsyncMock(return_value={"id": 42, "make": "Kia", "model": "Carnival"}))
    cb = _cb()
    asyncio.run(h.analyze_from_card(cb))
    mock.assert_awaited_once_with(42, 777)
    assert cb.answered
    assert len(cb.message.edits) == 1
    text, kb = cb.message.edits[0]
    assert "🟢" in text
    assert kb.inline_keyboard[0][0].web_app.url.endswith("/listing/42")


def test_analyze_quota_exceeded_shows_upsell(monkeypatch):
    monkeypatch.setattr(h, "_miniapp_url", lambda: "https://x:8443")
    monkeypatch.setattr(h.backend, "analyze_price", AsyncMock(return_value={"quota_exceeded": True}))
    cb = _cb()
    asyncio.run(h.analyze_from_card(cb))
    text, kb = cb.message.edits[0]
    assert "уже использован" in text and "Pro" in text
    assert kb.inline_keyboard[0][0].web_app.url.endswith("/listing/42")


def test_analyze_backend_down_polite_message(monkeypatch):
    monkeypatch.setattr(h, "_miniapp_url", lambda: "https://x:8443")
    monkeypatch.setattr(h.backend, "analyze_price", AsyncMock(return_value=None))
    cb = _cb()
    asyncio.run(h.analyze_from_card(cb))
    text, _ = cb.message.edits[0]
    assert "недоступен" in text


def test_analyze_bad_data_ignored():
    cb = FakeCb("analyze:oops")
    asyncio.run(h.analyze_from_card(cb))
    assert cb.message.answers == [] and cb.message.edits == []
