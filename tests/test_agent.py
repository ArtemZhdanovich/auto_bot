"""Тесты agent loop: tools, FAQ без LLM, ранний выход, ошибки → DialogError."""

import pytest

from app.ai.agent import DialogError, run_agent
from app.ai.llm_client import LLMError, LLMResult, MockLLMClient, ToolCall
from app.schemas.criteria import SearchCriteria
from app.services.dialog import DialogStore, new_state

from conftest import FakeRedis


def _llm(*results: LLMResult) -> MockLLMClient:
    return MockLLMClient(script=list(results))


@pytest.mark.asyncio
async def test_update_criteria_and_clarification(fake_redis: FakeRedis, fake_session):
    llm = _llm(
        LLMResult(
            tool_calls=[
                ToolCall(
                    id="1",
                    name="update_criteria",
                    arguments={"make": "Hyundai", "model": "Palisade"},
                ),
                ToolCall(
                    id="2",
                    name="ask_clarification",
                    arguments={
                        "field": "price_max",
                        "question": "Какой бюджет?",
                        "options": ["до 30к", "до 45к"],
                    },
                ),
            ]
        )
    )
    reply = await run_agent(1, "Хочу Palisade", session=fake_session, redis=fake_redis, llm=llm)

    # последним отработал ask_clarification → текст = уточняющий вопрос
    assert "бюджет" in reply.text.lower()
    assert reply.options == ["до 30к", "до 45к"]
    assert reply.criteria["make"] == "Hyundai"
    assert reply.finalize_ready is False
    assert reply.turn_count == 1

    state = await DialogStore(fake_redis).load(1)
    assert state.criteria.model == "Palisade"
    assert state.asked_fields == ["price_max"]

    # LLM вызван со списком tools (п. 7.3)
    _, tools = llm.calls[0]
    assert tools and {t["name"] for t in tools} >= {"update_criteria", "finalize_search"}


@pytest.mark.asyncio
async def test_faq_answered_without_llm(fake_redis: FakeRedis, fake_session):
    llm = MockLLMClient()
    reply = await run_agent(
        2, "Сколько стоит растаможка из Кореи?", session=fake_session, redis=fake_redis, llm=llm
    )
    assert llm.calls == []  # FAQ из кэша, LLM не тратим (п. 7.8)
    assert "растаможка" in reply.text.lower()


@pytest.mark.asyncio
async def test_early_exit_when_critical_fields_filled(fake_redis: FakeRedis, fake_session):
    store = DialogStore(fake_redis)
    state = new_state(3)
    state.criteria = SearchCriteria(make="Kia", price_max=45000, country="KR")
    await store.save(state)

    llm = MockLLMClient()
    reply = await run_agent(3, "всё", session=fake_session, redis=fake_redis, llm=llm)

    assert llm.calls == []  # ранний выход без LLM (п. 7.7.6)
    assert reply.status == "ready"
    assert reply.finalize_ready is True
    assert reply.options == ["🔍 Искать"]


@pytest.mark.asyncio
async def test_finalize_tool(fake_redis: FakeRedis, fake_session):
    llm = _llm(
        LLMResult(
            tool_calls=[
                ToolCall(
                    id="1",
                    name="finalize_search",
                    arguments={
                        "criteria": {"make": "Kia", "price_max": 30000, "country": "KR"}
                    },
                )
            ]
        )
    )
    enqueued: list[SearchCriteria] = []

    async def fin(criteria: SearchCriteria) -> list[str]:
        enqueued.append(criteria)
        return ["encar"]

    reply = await run_agent(
        4, "запускай", session=fake_session, redis=fake_redis, llm=llm, finalize=fin
    )

    assert reply.status == "searching"
    assert reply.finalize_ready is False
    assert reply.options == []  # кнопки поиска больше не нужны
    assert enqueued and enqueued[0].make == "Kia"
    state = await DialogStore(fake_redis).load(4)
    assert state.status == "searching"


@pytest.mark.asyncio
async def test_preview_search_empty(fake_redis: FakeRedis, fake_session):
    llm = _llm(
        LLMResult(
            tool_calls=[
                ToolCall(
                    id="1",
                    name="preview_search",
                    arguments={"criteria": {"make": "Kia"}},
                )
            ]
        )
    )
    reply = await run_agent(5, "что есть?", session=fake_session, redis=fake_redis, llm=llm)
    assert "ничего не нашлось" in reply.text.lower()


@pytest.mark.asyncio
async def test_llm_failure_raises_dialog_error(fake_redis: FakeRedis, fake_session):
    class FailingClient:
        async def complete(self, messages, tools=None) -> LLMResult:
            raise LLMError("boom")

        async def aclose(self) -> None:
            pass

    with pytest.raises(DialogError):
        await run_agent(6, "хочу машину", session=fake_session, redis=fake_redis, llm=FailingClient())


@pytest.mark.asyncio
async def test_unknown_tool_is_handled(fake_redis: FakeRedis, fake_session):
    llm = _llm(LLMResult(tool_calls=[ToolCall(id="1", name="delete_everything", arguments={})]))
    reply = await run_agent(7, "сделай что-нибудь", session=fake_session, redis=fake_redis, llm=llm)
    assert "переформулируйте" in reply.text.lower()  # не падаем на неизвестном tool


@pytest.mark.asyncio
async def test_clarification_options_from_catalog(fake_redis: FakeRedis, fake_session):
    """Справочные поля: кнопки только из FIELD_OPTIONS — LLM не выдумывает."""
    llm = _llm(
        LLMResult(
            tool_calls=[
                ToolCall(
                    id="1",
                    name="ask_clarification",
                    arguments={
                        "field": "body_type",
                        "question": "Какой кузов?",
                        "options": ["кроссовер", "лимузин"],
                    },
                )
            ]
        )
    )
    reply = await run_agent(9, "хочу кроссовер", session=fake_session, redis=fake_redis, llm=llm)
    from app.ai.tools import FIELD_OPTIONS

    assert reply.options == FIELD_OPTIONS["body_type"]
    assert "кроссовер" not in reply.options and "лимузин" not in reply.options

    # несправочное поле — опции LLM проходят как есть
    llm2 = _llm(
        LLMResult(
            tool_calls=[
                ToolCall(
                    id="1",
                    name="ask_clarification",
                    arguments={
                        "field": "price_max",
                        "question": "Бюджет?",
                        "options": ["до 30к", "до 45к"],
                    },
                )
            ]
        )
    )
    reply2 = await run_agent(9, "не знаю", session=fake_session, redis=fake_redis, llm=llm2)
    assert reply2.options == ["до 30к", "до 45к"]


# ---------- tool calc_delivery (MVP: расчёт «под ключ» силами агента) ----------


@pytest.mark.asyncio
async def test_calc_delivery_tool_returns_breakdown(fake_redis: FakeRedis, fake_session):
    """Агент считает смету инструментом, а не выдумывает цифры (п. 7.3)."""
    from app.ai.agent import ToolContext, _execute_tool
    from app.services.dialog import new_state

    state = new_state(11)
    ctx = ToolContext(session=fake_session, redis=fake_redis)
    outcome = await _execute_tool(
        ToolCall(
            id="1",
            name="calc_delivery",
            arguments={"price_usd": 18000, "engine_cc": 2000, "year": 2022},
        ),
        state,
        ctx,
    )
    assert outcome.text is not None
    assert "Итого" in outcome.text
    assert "Корея → Владивосток" in outcome.text
    assert "18 000 $" in outcome.text
    assert outcome.text.count("• ") == 7  # полная разбивка, не пересказ LLM


@pytest.mark.asyncio
async def test_calc_delivery_tool_bad_args(fake_redis: FakeRedis, fake_session):
    """Без цены/года — вежливая просьба уточнить, без расчёта и без падения."""
    from app.ai.agent import ToolContext, _execute_tool
    from app.services.dialog import new_state

    state = new_state(12)
    ctx = ToolContext(session=fake_session, redis=fake_redis)
    outcome = await _execute_tool(
        ToolCall(id="2", name="calc_delivery", arguments={"year": 2022}),
        state,
        ctx,
    )
    assert "уточните" in (outcome.text or "").lower()


def test_calc_delivery_tool_in_tools_schema():
    """Tool объявлен в схеме для LLM: year обязателен, engine_cc опционален."""
    from app.ai.tools import TOOLS

    tool = next(t for t in TOOLS if t["name"] == "calc_delivery")
    props = tool["parameters"]["properties"]
    assert set(props) == {"price_usd", "engine_cc", "year", "power_hp"}
    assert tool["parameters"]["required"] == ["price_usd", "year"]



# ---------- «мягкий» поиск и контекст диалога (фидбек: не завязанный бот) ----------


def test_is_confirmation():
    from app.ai.agent import _is_confirmation

    assert _is_confirmation("всё")
    assert _is_confirmation("Давай!")
    assert _is_confirmation("давай искать")
    assert not _is_confirmation("плохо ищешь")
    assert not _is_confirmation("а есть подешевле?")
    assert not _is_confirmation("найди kia carnival до 30к для семьи")


@pytest.mark.asyncio
async def test_criticism_reaches_llm_not_template(fake_redis: FakeRedis, fake_session):
    """Плохо ищешь» при заполненных критериях уходит в LLM (правка критериев),
    а не в заготовку «Всё главное собрал»."""
    store = DialogStore(fake_redis)
    state = new_state(21)
    state.criteria = SearchCriteria(make="Kia", price_max=30000, country="KR")
    await store.save(state)

    llm = _llm(
        LLMResult(
            tool_calls=[
                ToolCall(id="1", name="update_criteria", arguments={"color": None})
            ]
        )
    )
    reply = await run_agent(21, "плохо ищешь", session=fake_session, redis=fake_redis, llm=llm)

    assert llm.calls  # LLM был вызван, раннего выхода не случилось
    assert "Всё главное собрал" not in reply.text


@pytest.mark.asyncio
async def test_preview_relaxes_secondary_filters(fake_redis: FakeRedis, fake_session):
    """"Пусто по точным критериям → ослабляем (цвет) и показываем близкие варианты."""
    from app.db.models import Listing

    hit = Listing(
        id=1, source="encar", source_id="1", url="u", make="Kia",
        model="Carnival", year=2022, price=29000, currency="$", mileage_km=50000,
        color="white",
    )
    fake_session.set_results([[], [hit]])  # точный поиск пуст, без цвета — есть

    state = new_state(22)
    state.criteria = SearchCriteria(
        make="Kia", price_max=30000, color="black", country="KR"
    )
    await DialogStore(fake_redis).save(state)

    llm = _llm(
        LLMResult(
            tool_calls=[ToolCall(id="1", name="preview_search", arguments={})]
        )
    )
    reply = await run_agent(22, "что есть?", session=fake_session, redis=fake_redis, llm=llm)

    assert "ослабил" in reply.text.lower()
    assert "Kia Carnival" in reply.text
    # критерии пользователя не тронуты — color остался black
    state2 = await DialogStore(fake_redis).load(22)
    assert state2.criteria.color == "black"


@pytest.mark.asyncio
async def test_preview_nothing_found_suggests_relax(fake_redis: FakeRedis, fake_session):
    """"Пусто даже с ослаблением — не отписка, а предложение ослабить/финализировать."""
    fake_session.set_results([[]])
    llm = _llm(
        LLMResult(
            tool_calls=[ToolCall(id="1", name="preview_search", arguments={})]
        )
    )
    reply = await run_agent(23, "что есть?", session=fake_session, redis=fake_redis, llm=llm)
    assert "ничего не нашлось" in reply.text.lower()
    assert "ослабить" in reply.text.lower()


# ---------- квота AI: списываем за выдачу результата, не за сообщения ----------


def _hit() -> "object":
    from app.db.models import Listing

    return Listing(
        id=9, source="encar", source_id="9", url="u", make="Kia",
        model="Carnival", year=2022, price=29000, currency="$", mileage_km=50000,
    )


@pytest.mark.asyncio
async def test_quota_consumed_on_result_not_messages(fake_redis: FakeRedis, fake_session):
    """Сообщения/уточнения бесплатны; квота тратится только на выданные варианты."""
    from app.services.ai_quota import quota_used

    fake_session.set_results([[_hit()]])
    llm = _llm(
        LLMResult(tool_calls=[ToolCall(id="1", name="update_criteria", arguments={"make": "Kia"})]),
        LLMResult(tool_calls=[ToolCall(id="2", name="preview_search", arguments={})]),
        LLMResult(tool_calls=[ToolCall(id="3", name="preview_search", arguments={})]),
    )
    common = dict(session=fake_session, redis=fake_redis, llm=llm, quota_limit=1)

    await run_agent(31, "хочу карнивал", **common)          # сообщение — бесплатно
    assert await quota_used(fake_redis, 31) == 0

    reply = await run_agent(31, "покажи", **common)          # выдача вариантов — 1 поиск
    assert await quota_used(fake_redis, 31) == 1
    assert "ослабил" not in reply.text.lower()

    reply2 = await run_agent(31, "ещё", **common)            # лимит исчерпан
    assert "лимит" in reply2.text.lower()
    assert await quota_used(fake_redis, 31) == 1  # блокировка не списывает


@pytest.mark.asyncio
async def test_finalize_consumes_quota(fake_redis: FakeRedis, fake_session):
    from app.services.ai_quota import quota_used

    llm = _llm(
        LLMResult(
            tool_calls=[
                ToolCall(
                    id="1",
                    name="finalize_search",
                    arguments={"criteria": {"make": "Kia", "price_max": 30000, "country": "KR"}},
                )
            ]
        )
    )
    reply = await run_agent(
        32, "запускай", session=fake_session, redis=fake_redis, llm=llm,
        quota_limit=3,
    )
    assert reply.status == "searching"
    assert await quota_used(fake_redis, 32) == 1


@pytest.mark.asyncio
async def test_quota_none_is_unlimited(fake_redis: FakeRedis, fake_session):
    """Business (quota_limit=None) — не блокируется и не считается."""
    from app.services.ai_quota import quota_used

    fake_session.set_results([[_hit()]])
    llm = _llm(
        LLMResult(tool_calls=[ToolCall(id="1", name="preview_search", arguments={})]),
        LLMResult(tool_calls=[ToolCall(id="2", name="preview_search", arguments={})]),
    )
    await run_agent(33, "что есть?", session=fake_session, redis=fake_redis, llm=llm, quota_limit=None)
    await run_agent(33, "ещё раз", session=fake_session, redis=fake_redis, llm=llm, quota_limit=None)
    assert await quota_used(fake_redis, 33) == 0  # без лимита счётчик не пишем
