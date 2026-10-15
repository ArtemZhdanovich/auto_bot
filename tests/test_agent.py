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
    assert set(props) == {"price_usd", "engine_cc", "year"}
    assert tool["parameters"]["required"] == ["price_usd", "year"]

