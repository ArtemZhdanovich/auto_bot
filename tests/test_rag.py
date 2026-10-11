"""Тесты RAG-клиента и интеграции answer_question (план внедрения RAG, фазы 3-4)."""

import json

import httpx
import pytest

from app.ai.agent import run_agent
from app.ai.llm_client import LLMResult, MockLLMClient, ToolCall
from app.ai.rag_client import RAGClient, RAGUnavailable
from app.schemas.rag import RAGAnswer, RAGSource

from conftest import FakeRedis


def _rag_payload(answer="Растаможка считается по единой ставке ЕАЭС.", confidence=0.9):
    return {
        "answer": answer,
        "sources": [
            {
                "doc_id": "customs_kr_2024",
                "chunk_id": "chunk_12",
                "score": 0.87,
                "title": "Правила растаможки Корея → РФ",
                "snippet": "...",
            }
        ],
        "confidence": confidence,
        "latency_ms": 1240,
        "cached": False,
    }


def make_client(handler, *, base_url="http://rag.internal:8100", redis=None, **kwargs):
    requests: list[httpx.Request] = []

    def recording_handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return handler(request)

    client = RAGClient(
        base_url=base_url,
        api_token="rag-token-1",
        timeout=5,
        redis=redis,
        transport=httpx.MockTransport(recording_handler),
        **kwargs,
    )
    return client, requests


async def test_ask_parses_answer_and_sends_bearer():
    client, requests = make_client(
        lambda request: httpx.Response(200, json=_rag_payload())
    )
    answer = await client.ask("Сколько стоит растаможка?")

    assert answer.answer.startswith("Растаможка")
    assert answer.confidence == 0.9
    assert answer.sources[0].doc_id == "customs_kr_2024"
    assert answer.cached is False
    assert requests[0].url.path == "/api/v1/ask"
    assert requests[0].headers["Authorization"] == "Bearer rag-token-1"
    body = json.loads(requests[0].content)
    assert body["question"] == "Сколько стоит растаможка?"


async def test_ask_disabled_client_raises():
    client = RAGClient(base_url=None)
    with pytest.raises(RAGUnavailable):
        await client.ask("вопрос")


async def test_ask_uses_cache_on_second_call(fake_redis: FakeRedis):
    client, requests = make_client(
        lambda request: httpx.Response(200, json=_rag_payload()), redis=fake_redis
    )
    first = await client.ask("вопрос")
    second = await client.ask("вопрос")  # тот же вопрос → кэш

    assert len(requests) == 1  # HTTP был только один раз
    assert second.answer == first.answer
    assert second.cached is True
    # TTL записан (план п. 4, фаза 3: кэш Redis, TTL 1 час)
    key = [k for k in fake_redis.data if k.startswith("rag:answer:")][0]
    assert fake_redis.ttls[key] == 3600


async def test_ask_retries_on_network_error_then_succeeds():
    calls = {"n": 0}

    def flaky(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("boom")
        return httpx.Response(200, json=_rag_payload())

    client, _ = make_client(flaky)
    answer = await client.ask("вопрос")
    assert calls["n"] == 2
    assert answer.answer.startswith("Растаможка")


async def test_ask_http_error_does_not_retry():
    calls = {"n": 0}

    def forbidden(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(403)

    client, _ = make_client(forbidden)
    with pytest.raises(RAGUnavailable, match="rag_http_403"):
        await client.ask("вопрос")
    assert calls["n"] == 1  # 4xx не ретраим


async def test_circuit_breaker_opens_after_threshold():
    client, requests = make_client(lambda request: httpx.Response(500))
    for i in range(5):
        with pytest.raises(RAGUnavailable):
            await client.ask(f"вопрос-{i}")  # разные вопросы — мимо кэша
    assert len(requests) == 5  # каждый по одному запросу (5xx не ретраим)

    # breaker открыт: HTTP-запросов больше нет (план п. 4, фаза 3: 5 ошибок → пауза)
    with pytest.raises(RAGUnavailable, match="circuit_breaker_open"):
        await client.ask("ещё вопрос")
    assert len(requests) == 5


async def test_healthcheck_true_on_200_false_on_error():
    ok, _ = make_client(lambda request: httpx.Response(200, json={"status": "ok"}))
    assert await ok.healthcheck() is True

    down, _ = make_client(lambda request: httpx.Response(503))
    assert await down.healthcheck() is False


async def test_rag_answer_schema_defaults():
    a = RAGAnswer(answer="текст")
    assert a.sources == [] and a.confidence == 0.0 and a.cached is False
    s = RAGSource(doc_id="d", chunk_id="c", score=0.5)
    assert s.title is None and s.snippet is None


# --- интеграция answer_question в agent loop ------------------------------


class FakeRAG:
    """Стаб RAG-клиента для тестов agent loop."""

    def __init__(self, answer="Ответ из базы знаний", confidence=0.9, error=False):
        self.answer = answer
        self.confidence = confidence
        self.error = error
        self.enabled = True
        self.calls: list[tuple[str, dict]] = []

    async def ask(self, question, context=None, **kwargs):
        self.calls.append((question, context or {}))
        if self.error:
            raise RAGUnavailable("rag_network_error")
        return RAGAnswer(answer=self.answer, confidence=self.confidence, sources=[])


def _answer_tool_call(**extra) -> LLMResult:
    args = {"topic": "customs", "question": "Сколько растаможка Palisade?"}
    args.update(extra)
    return LLMResult(
        tool_calls=[ToolCall(id="1", name="answer_question", arguments=args)]
    )


async def test_answer_question_uses_rag(fake_redis: FakeRedis, fake_session):
    rag = FakeRAG(answer="Ставка ЕАЭС: 54% ...")
    llm = MockLLMClient(script=[_answer_tool_call()])
    reply = await run_agent(
        21, "Palisade 2022 дизель, растаможка из Кореи интересует", session=fake_session, redis=fake_redis,
        llm=llm, rag=rag,
    )
    assert reply.text == "Ставка ЕАЭС: 54% ..."
    assert len(rag.calls) == 1
    question, ctx = rag.calls[0]
    assert "растаможка" in question.lower()
    assert ctx["tenant"] == "autoselect"
    assert ctx["user_id"] == 21
    assert ctx["filters"] == {"topic": "customs"}


async def test_answer_question_falls_back_to_llm_answer(fake_redis: FakeRedis, fake_session):
    rag = FakeRAG(error=True)  # RAG упал
    llm = MockLLMClient(
        script=[_answer_tool_call(answer="Примерно 3-5 млн вон, точно скажет менеджер.")]
    )
    reply = await run_agent(
        22, "Palisade 2022, растаможка интересует", session=fake_session, redis=fake_redis, llm=llm, rag=rag
    )
    assert reply.text == "Примерно 3-5 млн вон, точно скажет менеджер."


async def test_answer_question_no_answer_refers_manager(fake_redis: FakeRedis, fake_session):
    rag = FakeRAG(error=True)
    llm = MockLLMClient(script=[_answer_tool_call()])  # answer не передан
    reply = await run_agent(
        23, "Palisade 2022, растаможка интересует", session=fake_session, redis=fake_redis, llm=llm, rag=rag
    )
    assert "менеджер" in reply.text.lower()


async def test_answer_question_low_confidence_falls_back(fake_redis: FakeRedis, fake_session):
    rag = FakeRAG(answer="Сомнительный ответ", confidence=0.3)
    llm = MockLLMClient(script=[_answer_tool_call(answer="Свой ответ LLM")])
    reply = await run_agent(
        24, "Palisade 2022, растаможка интересует", session=fake_session, redis=fake_redis, llm=llm, rag=rag
    )
    # confidence < 0.6 → ответ RAG отброшен (план п. 8.1)
    assert reply.text == "Свой ответ LLM"


async def test_answer_question_without_rag_uses_llm_answer(fake_redis: FakeRedis, fake_session):
    llm = MockLLMClient(script=[_answer_tool_call(answer="Ответ без RAG")])
    reply = await run_agent(
        25, "Palisade 2022, растаможка интересует", session=fake_session, redis=fake_redis, llm=llm, rag=None
    )
    assert reply.text == "Ответ без RAG"
