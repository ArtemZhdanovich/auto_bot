"""Хелперы калькулятора и фото-карточек в боте (MVP): цены в $, сметы, кнопки.

Вход через Telegram: /start login_{code} (веб-логин) — тесты в конце файла."""

from dataclasses import asdict
from types import SimpleNamespace
from unittest.mock import AsyncMock

import asyncio

import pytest

from aiogram.filters import CommandObject

from app.domain.calc.delivery import calc_delivery

import app.transport.bot.handlers as h
from app.transport.bot.handlers import (
    _car_card_kb,
    _format_calc_out,
    _miniapp_button,
    _miniapp_kb,
    _price_usd,
    build_listing_caption,
)


# ---------- _price_usd: конвертация KRW/EUR → USD ----------


def test_price_usd_krw_divides_by_1350():
    assert _price_usd({"price": 1_350_000, "currency": "KRW"}) == 1000


def test_price_usd_eur_multiplies():
    assert _price_usd({"price": 1000, "currency": "EUR"}) == 1080


def test_price_usd_usd_passthrough_and_round():
    assert _price_usd({"price": 123.6, "currency": "USD"}) == 124
    assert _price_usd({"price": 999, "currency": None}) == 999


def test_price_usd_garbage():
    assert _price_usd({"price": None, "currency": "KRW"}) is None
    assert _price_usd({"price": 0, "currency": "KRW"}) is None
    assert _price_usd({"price": "abc", "currency": "USD"}) is None
    assert _price_usd({}) is None


# ---------- _format_calc_out: смета для бота ----------


def _calc_out() -> dict:
    return asdict(calc_delivery(18_000, 2_000, 2022, current_year=2026))


def test_format_calc_out_contains_total_and_components():
    text = _format_calc_out(_calc_out())
    assert "Корея → Владивосток" in text
    assert "Итого" in text
    assert "18 000 $" in text  # цена авто с группировкой
    assert "2 000 см³" in text or "2000 см³" in text
    # по строке на каждый из 7 компонентов
    assert text.count("• ") == 7


def test_format_calc_out_disclaimer():
    assert "физлица" in _format_calc_out(_calc_out()) or "Оценка" in _format_calc_out(
        _calc_out()
    )


# ---------- _miniapp_button: путь внутри мини-аппа ----------


def test_miniapp_button_path_https(monkeypatch):
    monkeypatch.setattr(h, "_miniapp_url", lambda: "https://10.0.0.1:8443")
    btn = _miniapp_button("🧮 Открыть", "/calc?price=18000&cc=2000&year=2022")
    assert btn is not None
    assert btn.web_app is not None
    assert btn.web_app.url == "https://10.0.0.1:8443/calc?price=18000&cc=2000&year=2022"


def test_miniapp_button_path_http_fallback(monkeypatch):
    """http → url-кнопка, путь тоже добавлен."""
    monkeypatch.setattr(h, "_miniapp_url", lambda: "http://192.168.1.5:8080")
    btn = _miniapp_button(path="/calc")
    assert btn is not None
    assert btn.url == "http://192.168.1.5:8080/calc"
    assert btn.web_app is None


def test_miniapp_kb_with_path(monkeypatch):
    monkeypatch.setattr(h, "_miniapp_url", lambda: "https://x.example")
    kb = _miniapp_kb("/calc")
    assert kb is not None
    assert len(kb.inline_keyboard) == 1
    assert kb.inline_keyboard[0][0].web_app.url == "https://x.example/calc"


def test_miniapp_kb_none_without_url(monkeypatch):
    monkeypatch.setattr(h, "_miniapp_url", lambda: "")
    assert _miniapp_kb("/calc") is None


# ---------- _car_card_kb: кнопки фото-карточки ----------


def test_car_card_kb_full(monkeypatch):
    monkeypatch.setattr(h, "_miniapp_url", lambda: "https://x.example")
    kb = _car_card_kb(
        {"id": 42, "url": "https://encar.com/xxx", "images": ["https://img/1.jpg"]}
    )
    texts = [b.text for row in kb.inline_keyboard for b in row]
    datas = [b.callback_data for row in kb.inline_keyboard for b in row]
    urls = [b.url for row in kb.inline_keyboard for b in row if b.url]
    assert "🔗 Открыть объявление" in texts
    assert "🚚 Доставка и растаможка" in texts
    assert "calc:42" in datas
    assert "https://encar.com/xxx" in urls


def test_car_card_kb_without_url_and_miniapp(monkeypatch):
    monkeypatch.setattr(h, "_miniapp_url", lambda: "")
    kb = _car_card_kb({"id": 7, "url": None})
    texts = [b.text for row in kb.inline_keyboard for b in row]
    assert "🔗 Открыть объявление" not in texts
    assert "calc:7" in [b.callback_data for row in kb.inline_keyboard for b in row]
    # мини-апп кнопки нет, но доставка осталась
    assert "🚚 Доставка и растаможка" in texts


# ---------- build_listing_caption ----------


def test_caption_krw_price_grouped():
    caption = build_listing_caption(
        {
            "make": "Hyundai",
            "model": "팰리세이드",
            "year": 2022,
            "price": 18_400_000,
            "currency": "KRW",
            "mileage_km": 60_000,
            "source": "encar",
        }
    )
    assert "<b>Hyundai 팰리세이드</b>" in caption
    assert "18 400 000 KRW" in caption
    assert "2022" in caption and "60 000 км" in caption
    assert "encar" in caption


def test_caption_without_price():
    caption = build_listing_caption({"make": "Kia", "model": "K5"})
    assert "цена не указана" in caption
    assert len(caption) <= 1024


# ---------- _parse_variants / _format_compare_out / menu:compare ----------


def test_parse_variants_vs():
    assert h._parse_variants("18000 2000 2022 vs 15000 1600 2021") == [
        (18000.0, 2000, 2022),
        (15000.0, 1600, 2021),
    ]


def test_parse_variants_semicolons_and_newlines():
    assert h._parse_variants("18000 2000 2022; 15000 2021\n20000 3000 2023") == [
        (18000.0, 2000, 2022),
        (15000.0, 2000, 2021),
        (20000.0, 3000, 2023),
    ]


def test_parse_variants_defaults_cc_and_ignores_garbage():
    # только цена+год -> cc=2000; мусорный чанк без чисел пропущен
    assert h._parse_variants("абв\n18000 2022") == [(18000.0, 2000, 2022)]
    assert h._parse_variants("привет") == []


def test_parse_variants_rejects_bad_ranges():
    assert h._parse_variants("0 2000 2022 vs 15000 99999 2021 vs 15000 2000 1800") == []


def test_format_compare_out_medals_and_delta():
    items = [
        {"title": "A", "price_usd": 15000, "total_usd": 30000, "total_rub": 2760000, "age_group": "3-5 лет"},
        {"title": "B", "price_usd": 18000, "total_usd": 32500, "total_rub": 2990000, "age_group": "до 3 лет"},
    ]
    out = h._format_compare_out(items)
    assert "\U0001F947" in out and "A" in out
    assert "+2 500 $" in out  # дельта к лучшему, разделители — пробелы
    assert "30 000" in out and "32 500" in out


def test_main_menu_has_compare_button():
    kb = h.build_main_menu_kb()
    datas = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert "menu:compare" in datas and "menu:calc" in datas


def test_compare_delivery_client_post():
    import asyncio
    from unittest.mock import AsyncMock

    h.backend._post = AsyncMock(return_value={"items": []})
    asyncio.run(h.backend.compare_delivery([{"price_usd": 1, "engine_cc": 2, "year": 3}]))
    path, payload = h.backend._post.call_args.args
    assert path == "/api/calc/compare"
    assert payload["items"][0]["price_usd"] == 1


# ---------- cmd_calc / main_menu_callback: интеграция хендлеров ----------


class FakeMsg:
    def __init__(self):
        self.answers = []

    async def answer(self, text, reply_markup=None):
        self.answers.append((text, reply_markup))


class FakeCb:
    def __init__(self, data):
        self.data = data
        self.from_user = SimpleNamespace(id=777)
        self.message = FakeMsg()
        self.answered = False

    async def answer(self, *a, **kw):
        self.answered = True


def _cmd(args):
    return CommandObject(command="calc", args=args)


def test_cmd_calc_single_variant(monkeypatch):
    monkeypatch.setattr(h, "_miniapp_url", lambda: "https://x:8443")
    mock = AsyncMock(return_value=_calc_out())
    monkeypatch.setattr(h.backend, "calc_delivery", mock)
    msg = FakeMsg()
    asyncio.run(h.cmd_calc(msg, _cmd("18000 2000 2022")))
    mock.assert_awaited_once_with(18000.0, 2000, 2022)
    text, kb = msg.answers[0]
    assert "\U0001F947" not in text  # не сравнение
    assert kb.inline_keyboard[0][0].web_app.url.endswith("/calc?price=18000&cc=2000&year=2022")


def test_cmd_calc_two_numbers_cc_defaults(monkeypatch):
    monkeypatch.setattr(h, "_miniapp_url", lambda: "https://x:8443")
    mock = AsyncMock(return_value=_calc_out())
    monkeypatch.setattr(h.backend, "calc_delivery", mock)
    msg = FakeMsg()
    asyncio.run(h.cmd_calc(msg, _cmd("18000 2022")))
    mock.assert_awaited_once_with(18000.0, 2000, 2022)


def test_cmd_calc_grouped_thousands(monkeypatch):
    monkeypatch.setattr(h, "_miniapp_url", lambda: "https://x:8443")
    mock = AsyncMock(return_value=_calc_out())
    monkeypatch.setattr(h.backend, "calc_delivery", mock)
    msg = FakeMsg()
    asyncio.run(h.cmd_calc(msg, _cmd("18 000 2000 2022")))
    mock.assert_awaited_once_with(18000.0, 2000, 2022)


def test_cmd_calc_compare_variants(monkeypatch):
    monkeypatch.setattr(h, "_miniapp_url", lambda: "https://x:8443")
    items = [
        {"title": "B", "price_usd": 15000, "total_usd": 21904, "total_rub": 2015186, "age_group": "3\u20135 \u043B\u0435\u0442"},
        {"title": "A", "price_usd": 18000, "total_usd": 26416, "total_rub": 2430290, "age_group": "3\u20135 \u043B\u0435\u0442"},
    ]
    mock = AsyncMock(return_value={"items": items})
    monkeypatch.setattr(h.backend, "compare_delivery", mock)
    msg = FakeMsg()
    asyncio.run(h.cmd_calc(msg, _cmd("18000 2000 2022 vs 15000 1600 2021")))
    payload = mock.call_args.args[0]
    assert payload[0]["price_usd"] == 18000 and payload[1]["engine_cc"] == 1600
    text, kb = msg.answers[0]
    assert "\U0001F947" in text and "\U0001F948" in text
    assert kb.inline_keyboard[0][0].web_app.url.endswith("/compare")


def test_cmd_calc_compare_backend_down_falls_to_help(monkeypatch):
    monkeypatch.setattr(h, "_miniapp_url", lambda: "https://x:8443")
    monkeypatch.setattr(h.backend, "compare_delivery", AsyncMock(return_value=None))
    msg = FakeMsg()
    asyncio.run(h.cmd_calc(msg, _cmd("18000 2000 2022 vs 15000 1600 2021")))
    assert len(msg.answers) == 1
    assert "\u0446\u0435\u043D\u0430 $" in msg.answers[0][0]


def test_cmd_calc_garbage_shows_help(monkeypatch):
    monkeypatch.setattr(h, "_miniapp_url", lambda: "https://x:8443")
    msg = FakeMsg()
    asyncio.run(h.cmd_calc(msg, _cmd("\u043F\u0440\u0438\u0432\u0435\u0442")))
    assert "\u0446\u0435\u043D\u0430 $" in msg.answers[0][0]


def test_menu_compare_action(monkeypatch):
    monkeypatch.setattr(h, "_miniapp_url", lambda: "https://x:8443")
    cb = FakeCb("menu:compare")
    state = AsyncMock()
    asyncio.run(h.main_menu_callback(cb, state))
    text, kb = cb.message.answers[0]
    assert "\u0421\u0440\u0430\u0432\u043D\u0435\u043D\u0438\u0435" in text
    assert kb.inline_keyboard[0][0].web_app.url.endswith("/compare")
    assert cb.answered
    state.clear.assert_not_called()


def test_menu_ai_action_clears_state():
    cb = FakeCb("menu:ai")
    state = AsyncMock()
    asyncio.run(h.main_menu_callback(cb, state))
    state.clear.assert_awaited_once()
    assert len(cb.message.answers) == 1


# ---------- fallback_text: ссылка на объяву → карточка ----------


def test_extract_url_strips_punctuation():
    assert h._extract_url("глянь https://encar.com/car/1.") == "https://encar.com/car/1"
    assert h._extract_url("https://kcar.com/x?q=1, ок?") == "https://kcar.com/x?q=1"
    assert h._extract_url("просто текст") is None
    assert h._extract_url("") is None


def test_fallback_text_url_found_sends_card(monkeypatch):
    monkeypatch.setattr(h, "_miniapp_url", lambda: "https://x:8443")
    listing = {"id": 9, "make": "Kia", "model": "Carnival", "images": []}
    monkeypatch.setattr(
        h.backend, "get_listing_by_url", AsyncMock(return_value=listing)
    )
    cards = AsyncMock()
    monkeypatch.setattr(h, "_send_car_cards", cards)
    agent = AsyncMock()
    monkeypatch.setattr(h, "_agent_turn", agent)
    msg = FakeMsg()
    msg.text = "глянь https://encar.com/car/1"
    asyncio.run(h.fallback_text(msg, AsyncMock()))
    h.backend.get_listing_by_url.assert_awaited_once_with("https://encar.com/car/1")
    cards.assert_awaited_once()
    agent.assert_not_called()


def test_fallback_text_url_unknown_goes_to_agent(monkeypatch):
    monkeypatch.setattr(h, "_miniapp_url", lambda: "https://x:8443")
    monkeypatch.setattr(h.backend, "get_listing_by_url", AsyncMock(return_value=None))
    cards = AsyncMock()
    monkeypatch.setattr(h, "_send_car_cards", cards)
    agent = AsyncMock()
    monkeypatch.setattr(h, "_agent_turn", agent)
    msg = FakeMsg()
    msg.text = "глянь https://encar.com/car/1"
    asyncio.run(h.fallback_text(msg, AsyncMock()))
    cards.assert_not_called()
    agent.assert_awaited_once()


def test_fallback_text_plain_text_goes_to_agent(monkeypatch):
    monkeypatch.setattr(h, "_miniapp_url", lambda: "https://x:8443")
    get_by_url = AsyncMock()
    monkeypatch.setattr(h.backend, "get_listing_by_url", get_by_url)
    agent = AsyncMock()
    monkeypatch.setattr(h, "_agent_turn", agent)
    msg = FakeMsg()
    msg.text = "хочу машину до 20000"
    asyncio.run(h.fallback_text(msg, AsyncMock()))
    get_by_url.assert_not_called()
    agent.assert_awaited_once()


# ---------- /start login_{code}: вход на сайт через Telegram ----------
@pytest.mark.asyncio
async def test_login_deep_link_confirms_and_answers(monkeypatch):
    """login_{code} → confirm в backend, ответ «вход подтверждён», True."""
    confirm = AsyncMock(return_value={"status": "ok"})
    monkeypatch.setattr(h.backend, "confirm_login_link", confirm)
    message = SimpleNamespace(
        from_user=SimpleNamespace(
            id=42, username="u", first_name="Иван", last_name=None, language_code="ru"
        ),
        answer=AsyncMock(),
    )
    handled = await h.handle_deep_link(message, "login_abc12345")
    assert handled is True
    confirm.assert_awaited_once_with(
        code="abc12345",
        telegram_id=42,
        username="u",
        first_name="Иван",
        last_name=None,
        language_code="ru",
    )
    text = message.answer.await_args.args[0]
    assert "Вход подтверждён" in text


@pytest.mark.asyncio
async def test_login_deep_link_expired(monkeypatch):
    """Истёкший/неизвестный код → ответ про устаревшую ссылку, True."""
    confirm = AsyncMock(return_value=None)
    monkeypatch.setattr(h.backend, "confirm_login_link", confirm)
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=1, username=None, first_name="A", last_name=None, language_code=None),
        answer=AsyncMock(),
    )
    handled = await h.handle_deep_link(message, "login_deadbeef00")
    assert handled is True
    text = message.answer.await_args.args[0]
    assert "устарела" in text
