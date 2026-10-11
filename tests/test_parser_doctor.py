"""Тесты ParserDoctor — ИИ-ремонтника SSR-парсеров.

Сценарии: propose/auto/off режимы, cooldown, невалидные и мусорные ответы
LLM, ошибка LLM — во всех случаях конфиг не портится и исключений нет.
"""

import httpx

from app.adapters.che168 import Che168HtmlAdapter
from app.adapters.common.http import HttpClient
from app.ai.llm_client import LLMError, LLMResult, MockLLMClient
from app.db.models import Source
from app.services.parser_doctor import extract_json, repair_parser
from conftest import FakeRedis, FakeSession

# «Новая вёрстка» (то, к чему якобы переехал сайт) — старые селекторы тут не работают
NEW_LAYOUT = """
<html><body>
<div class="ad-list">
  <div class="ad-card" data-id="90001"><a href="/a/90001/">
    <h3 class="name">2021款 奥迪A4L</h3><span class="prc">19.5万</span></a>
  </div>
  <div class="ad-card" data-id="90002"><a href="/a/90002/">
    <h3 class="name">2022款 丰田凯美瑞</h3><span class="prc">17.8万</span></a>
  </div>
  <div class="ad-card" data-id="90003"><a href="/a/90003/">
    <h3 class="name">2020款 大众迈腾</h3><span class="prc">15.2万</span></a>
  </div>
</div>
</body></html>
"""

NEW_SELECTORS = {
    "card_selector": "div.ad-card",
    "title_selector": "h3.name",
    "price_selector": "span.prc",
    "link_selector": "a",
}

OLD_CONFIG = {
    "base_url": "https://www.che168.com",
    "list_path": "/china/a0_0msdgscncgpi1ltocsp1exb0/",
    "card_selector": "ul#usedList li",  # сломанные (старые)
    "title_selector": "h4",
    "price_selector": ".price",
    "link_selector": "a",
    "doctor_mode": "auto",
}


def make_adapter(html: str) -> Che168HtmlAdapter:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=html)

    return Che168HtmlAdapter(
        source_name="che168",
        config=OLD_CONFIG,
        http_client=HttpClient(transport=httpx.MockTransport(handler), timeout=10),
    )


def make_llm(payload: str) -> MockLLMClient:
    return MockLLMClient(script=[LLMResult(text=payload)])


def make_session(mode: str) -> FakeSession:
    session = FakeSession()
    config = {**OLD_CONFIG, "doctor_mode": mode}
    session.add(Source(name="che168", adapter_class="Che168HtmlAdapter",
                       kind="parser", config=config))
    return session


def json_of(data: dict) -> str:
    return str(data).replace("'", '"')


# --- extract_json ------------------------------------------------------


def test_extract_json_tolerates_fences_and_noise():
    text = 'Вот предложение:\n```json\n{"card_selector": "div.x", "a": 1}\n```\nГотово'
    assert extract_json(text) == {"card_selector": "div.x", "a": 1}
    assert extract_json("мусор без скобок") is None
    assert extract_json(None) is None
    assert extract_json("{сломанный json") is None


# --- основной сценарий -------------------------------------------------


async def test_auto_mode_repairs_config():
    llm = make_llm(json_of(NEW_SELECTORS))
    session = make_session("auto")
    result = await repair_parser(
        "che168", make_adapter(NEW_LAYOUT), session=session, redis=FakeRedis(), llm=llm
    )

    assert result["status"] == "repaired"
    assert result["items"] == 3
    assert session.committed is True
    source = session.added[0]
    for key, value in NEW_SELECTORS.items():
        assert source.config[key] == value
    # base_url и прочий конфиг не потерялись
    assert source.config["base_url"] == OLD_CONFIG["base_url"]
    assert len(llm.calls) == 1


async def test_suggest_mode_does_not_touch_db():
    llm = make_llm(json_of(NEW_SELECTORS))
    session = make_session("suggest")
    result = await repair_parser(
        "che168", make_adapter(NEW_LAYOUT), session=session, redis=FakeRedis(), llm=llm
    )

    assert result["status"] == "suggested"
    assert session.committed is False
    source = session.added[0]
    assert source.config["card_selector"] == OLD_CONFIG["card_selector"]  # старые


async def test_disabled_mode_no_llm_call():
    llm = make_llm("{}")
    session = make_session("off")
    result = await repair_parser(
        "che168", make_adapter(NEW_LAYOUT), session=session, redis=FakeRedis(), llm=llm
    )
    assert result["status"] == "disabled"
    assert llm.calls == []


# --- отказоустойчивость ------------------------------------------------


async def test_cooldown_skips_llm():
    llm = make_llm(json_of(NEW_SELECTORS))
    redis = FakeRedis()
    await redis.set("doctor:che168", "1", nx=True, ex=3600)
    session = make_session("auto")
    result = await repair_parser(
        "che168", make_adapter(NEW_LAYOUT), session=session, redis=redis, llm=llm
    )
    assert result["status"] == "cooldown"
    assert llm.calls == []


async def test_invalid_proposal_rejected():
    # LLM предлагает селекторы, которые на живом HTML не находят карточек
    bad = {k: "div.nonexistent" for k in NEW_SELECTORS}
    llm = make_llm(json_of(bad))
    session = make_session("auto")
    result = await repair_parser(
        "che168", make_adapter(NEW_LAYOUT), session=session, redis=FakeRedis(), llm=llm
    )
    assert result["status"] == "invalid"
    assert session.committed is False


async def test_duplicate_ids_rejected():
    html = NEW_LAYOUT.replace('data-id="90002"', 'data-id="90001"')
    llm = make_llm(json_of(NEW_SELECTORS))
    session = make_session("auto")
    result = await repair_parser(
        "che168", make_adapter(html), session=session, redis=FakeRedis(), llm=llm
    )
    assert result["status"] == "invalid"


async def test_llm_bad_output():
    llm = make_llm("Извините, я не понял задачу.")  # нет JSON
    session = make_session("auto")
    result = await repair_parser(
        "che168", make_adapter(NEW_LAYOUT), session=session, redis=FakeRedis(), llm=llm
    )
    assert result["status"] == "llm_bad_output"


async def test_llm_incomplete_json():
    llm = make_llm('{"card_selector": "div.ad-card"}')  # не все 4 ключа
    session = make_session("auto")
    result = await repair_parser(
        "che168", make_adapter(NEW_LAYOUT), session=session, redis=FakeRedis(), llm=llm
    )
    assert result["status"] == "llm_bad_output"


async def test_llm_error_does_not_raise():
    class BrokenLLM:
        async def complete(self, messages, tools=None):
            raise LLMError("GLM_API_KEY is not set")

    session = make_session("auto")
    result = await repair_parser(
        "che168", make_adapter(NEW_LAYOUT), session=session, redis=FakeRedis(),
        llm=BrokenLLM(),
    )
    assert result["status"] == "llm_error"
    assert session.committed is False


async def test_no_source_row():
    session = FakeSession()
    result = await repair_parser(
        "che168", make_adapter(NEW_LAYOUT), session=session, redis=FakeRedis(),
        llm=make_llm("{}"),
    )
    assert result["status"] == "no_source"


async def test_ban_403_triaged_without_llm():
    """403 = бан/геоблок: врач ставит диагноз, LLM не зовёт, конфиг не трогает."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403)

    adapter = Che168HtmlAdapter(
        source_name="che168",
        config=OLD_CONFIG,
        http_client=HttpClient(transport=httpx.MockTransport(handler), timeout=10),
    )
    llm = make_llm(json_of(NEW_SELECTORS))
    session = make_session("auto")
    result = await repair_parser(
        "che168", adapter, session=session, redis=FakeRedis(), llm=llm
    )
    assert result["status"] == "banned"
    assert session.committed is False
    assert llm.calls == []  # до LLM дело не дошло


async def test_server_down_5xx_triaged_without_llm():
    """5xx = источник лежит: это не вёрстка, LLM-ремонт не запускаем."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    adapter = Che168HtmlAdapter(
        source_name="che168",
        config=OLD_CONFIG,
        http_client=HttpClient(transport=httpx.MockTransport(handler), timeout=10),
    )
    llm = make_llm(json_of(NEW_SELECTORS))
    session = make_session("auto")
    result = await repair_parser(
        "che168", adapter, session=session, redis=FakeRedis(), llm=llm
    )
    assert result["status"] == "server_down"
    assert llm.calls == []
    assert session.committed is False


async def test_captcha_stub_page_triaged_banned():
    """HTTP 200, но вместо списка — заглушка антибота с капчей: это бан."""
    stub = "<html><body>请完成安全验证 to continue browsing" + "x" * 400 + "</body></html>"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=stub)

    adapter = Che168HtmlAdapter(
        source_name="che168",
        config=OLD_CONFIG,
        http_client=HttpClient(transport=httpx.MockTransport(handler), timeout=10),
    )
    llm = make_llm(json_of(NEW_SELECTORS))
    session = make_session("auto")
    result = await repair_parser(
        "che168", adapter, session=session, redis=FakeRedis(), llm=llm
    )
    assert result["status"] == "banned"
    assert llm.calls == []


async def test_server_down_does_not_consume_cooldown():
    """Бан/падение триажится каждый раз: cooldown ставится только перед LLM."""
    redis = FakeRedis()

    def down_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    down = Che168HtmlAdapter(
        source_name="che168",
        config=OLD_CONFIG,
        http_client=HttpClient(transport=httpx.MockTransport(down_handler), timeout=10),
    )
    for _ in range(2):  # cooldown не мешает повторному триажу
        result = await repair_parser(
            "che168", down, session=make_session("auto"), redis=redis, llm=None
        )
        assert result["status"] == "server_down"
    assert await redis.get("doctor:che168") is None  # cooldown не выставлен


# --- unit: классификаторы триажа ----------------------------------------


def test_classify_page_buckets():
    from app.services.parser_doctor import classify_page

    assert classify_page(NEW_LAYOUT) == "markup_change"
    assert classify_page("Access Denied") == "banned"  # короче MIN_HTML_LEN
    captcha = "captcha" + "y" * 400
    assert classify_page(captcha) == "banned"  # маркер антибота


def test_classify_fetch_error_buckets():
    from app.services.parser_doctor import classify_fetch_error

    assert classify_fetch_error("HTTP 403 for https://x") == "banned"
    assert classify_fetch_error("HTTP 429 for https://x") == "banned"
    assert classify_fetch_error("HTTP 502 for https://x") == "server_down"
    assert classify_fetch_error("Failed to fetch https://x: timeout") == "server_down"
    assert classify_fetch_error(None) == "server_down"
