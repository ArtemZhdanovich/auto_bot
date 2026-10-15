"""UI-панель диалога бота: форматы, кнопки, токены хода, мини-апп."""

from app.bot.handlers import (
    _miniapp_button,
    _search_ready,
    build_dialog_kb,
    format_criteria,
)


def test_format_criteria_human_readable_no_json():
    text = format_criteria(
        {"make": "Hyundai", "year_from": 2022, "price_max": 45000, "country": "CN"}
    )
    assert "{" not in text and "}" not in text
    assert "Марка: <b>Hyundai</b>" in text
    assert "Год от: <b>2022</b>" in text
    assert "45 000" in text
    assert "Китай" in text


def test_format_criteria_skips_empty():
    text = format_criteria({"make": None, "model": "", "sources": []})
    assert text == ""


def test_search_ready_heuristic():
    assert _search_ready({"make": "Kia", "price_max": 1, "country": "KR"})
    assert not _search_ready({"make": "Kia", "price_max": 1})  # нет страны
    assert not _search_ready({"country": "KR"})


def test_dialog_kb_has_turn_token_and_nav():
    kb = build_dialog_kb({"options": ["Корея", "Китай"]}, turn=3, criteria={})
    texts = [b.text for row in kb.inline_keyboard for b in row]
    assert "Корея" in texts and "Китай" in texts
    datas = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert "dlg:opt:3:0" in datas and "dlg:opt:3:1" in datas
    assert "dlg:finalize" not in datas  # критерии не заполнены
    assert "dlg:reset" in datas


def test_dialog_kb_shows_search_when_ready():
    kb = build_dialog_kb(
        {"options": [], "finalize_ready": False},
        turn=1,
        criteria={"make": "Kia", "price_max": 10000, "country": "KR"},
    )
    datas = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert "dlg:finalize" in datas


def test_miniapp_button_http_falls_back_to_url(monkeypatch):
    import app.bot.handlers as h

    monkeypatch.setattr(h, "_miniapp_url", lambda: "http://localhost:8080")
    btn = _miniapp_button()
    assert btn is not None
    assert btn.url == "http://localhost:8080"  # url-кнопка вместо web_app
    assert btn.web_app is None


def test_miniapp_button_https_uses_web_app(monkeypatch):
    import app.bot.handlers as h

    monkeypatch.setattr(h, "_miniapp_url", lambda: "https://app.example.com")
    btn = _miniapp_button()
    assert btn is not None and btn.web_app is not None
