"""Inline-режим «Поделиться» из миниаппа (ТЗ: карточка лота в чате + deep-link в вебапп)."""

from types import SimpleNamespace

import app.transport.bot.handlers as h


def _listing(**over):
    base = {
        "id": 77,
        "make": "Kia",
        "model": "Carnival",
        "year": 2022,
        "price": 18_400_000,
        "currency": "KRW",
        "mileage_km": 42_000,
        "body_type": "минивэн",
        "source": "kcar",
        "url": "https://www.kcar.com/bc/detail/CarInfoDtl?carCd=X1",
        "images": ["https://img/1.jpg", "https://img/2.webp"],
    }
    base.update(over)
    return base


def _urls(art) -> list[str]:
    return [b.url for row in art.reply_markup.inline_keyboard for b in row if b.url]


def test_share_article_has_startapp_button_and_source():
    art = h.build_share_article(_listing(), "ftadasd_bot")
    urls = _urls(art)
    # Ссылка карточки в вебапп: t.me/<bot>?startapp=listing_<id>
    assert "https://t.me/ftadasd_bot?startapp=listing_77" in urls
    assert any("kcar.com" in u for u in urls)
    assert "Kia Carnival, 2022" in art.title
    assert "Kia Carnival" in art.input_message_content.message_text


def test_share_article_without_username_no_app_button():
    art = h.build_share_article(_listing(), "")
    urls = _urls(art)
    assert not any("startapp" in u for u in urls)
    assert any("kcar.com" in u for u in urls)


def test_share_article_at_prefix_username():
    art = h.build_share_article(_listing(), "@ftadasd_bot")
    assert "https://t.me/ftadasd_bot?startapp=listing_77" in _urls(art)


def test_share_article_thumb_only_jpeg():
    art = h.build_share_article(_listing(), "bot")
    assert art.thumbnail_url == "https://img/1.jpg"
    art_webp = h.build_share_article(_listing(images=["https://img/x.webp"]), "bot")
    assert art_webp.thumbnail_url is None


def test_inline_query_regex_variants():
    for q in ("listing:77", "listing_77", "listing77", "LISTING:77", "listing-77"):
        m = h._LISTING_INLINE_QUERY.match(q)
        assert m, q
        assert m.group(1) == "77"
    assert not h._LISTING_INLINE_QUERY.match("какая машина?")
    assert not h._LISTING_INLINE_QUERY.match("listing:abc")


async def test_inline_handler_answers_card(monkeypatch):
    captured: dict = {}

    class FakeQuery:
        query = "listing:77"
        bot = None

        async def answer(self, results, cache_time=0, is_personal=False, **kw):
            captured["results"] = results
            captured["personal"] = is_personal

    async def fake_get_listing(listing_id: int):
        assert listing_id == 77
        return _listing()

    monkeypatch.setattr(h.backend, "get_listing", fake_get_listing)
    monkeypatch.setattr(
        "app.config.get_settings",
        lambda: SimpleNamespace(bot_username="ftadasd_bot"),
    )

    await h.inline_listing_share(FakeQuery())

    results = captured["results"]
    assert len(results) == 1
    assert results[0].id == "listing_77"
    assert any(
        "startapp=listing_77" in u for u in _urls(results[0])
    )
    assert captured["personal"] is True


async def test_inline_handler_empty_or_unknown_query(monkeypatch):
    captured: dict = {}

    class FakeQuery:
        query = "какая машина?"
        bot = None

        async def answer(self, results, cache_time=0, is_personal=False, **kw):
            captured["n"] = len(results)

    async def boom(*a, **kw):
        raise AssertionError("backend не должен дергаться для чужого запроса")

    monkeypatch.setattr(h.backend, "get_listing", boom)
    await h.inline_listing_share(FakeQuery())
    assert captured["n"] == 0


async def test_inline_handler_missing_listing(monkeypatch):
    captured: dict = {}

    class FakeQuery:
        query = "listing:404"
        bot = None

        async def answer(self, results, cache_time=0, is_personal=False, **kw):
            captured["n"] = len(results)

    async def fake_get_listing(listing_id: int):
        return None

    monkeypatch.setattr(h.backend, "get_listing", fake_get_listing)
    await h.inline_listing_share(FakeQuery())
    assert captured["n"] == 0
