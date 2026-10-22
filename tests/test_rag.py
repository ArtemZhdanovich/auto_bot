"""Тесты RAG-клиента и интеграции answer_question (план внедрения RAG, фазы 3-4)."""

import json

import httpx
import pytest

from app.infrastructure.ai.agent import run_agent
from app.infrastructure.ai.llm_client import LLMResult, MockLLMClient, ToolCall
from app.infrastructure.ai.rag_client import RAGClient, RAGUnavailable
from app.application.dto.rag import RAGAnswer, RAGSource

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
    # max_retries=0: проверяем именно breaker, а не политику ретраев.
    client, requests = make_client(lambda request: httpx.Response(500), max_retries=0)
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



# tests/ai/test_rag_client.py
from unittest.mock import AsyncMock, MagicMock, patch

from pydantic import SecretStr

from app.infrastructure.ai.rag_client import (
    _secret_or_none, rag_client_from_settings,
)


def test_secret_or_none_unwraps():
    assert _secret_or_none(SecretStr("x")) == "x"
    assert _secret_or_none("plain") == "plain"
    assert _secret_or_none(None) is None
    assert _secret_or_none(SecretStr("")) is None


def test_headers_do_not_contain_secret_mask():
    """Регрессия: SecretStr не должен давать 'Bearer **********'."""
    c = RAGClient(base_url="http://x", api_token=SecretStr("real-token"))
    headers = c._headers()
    assert headers["Authorization"] == "Bearer real-token"
    assert "**********" not in headers["Authorization"]


@pytest.mark.asyncio
async def test_ask_does_not_retry_4xx():
    """401 — клиентская ошибка, не ретраим и breaker не трогаем."""
    calls = {"n": 0}
    def handler(request):
        calls["n"] += 1
        return httpx.Response(401)
    c = RAGClient(
        base_url="http://x", max_retries=3,
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(RAGUnavailable, match="401"):
        await c.ask("q")
    assert calls["n"] == 1
    # Breaker не должен открыться от 4xx
    assert c._consecutive_failures == 0
    assert c._open_until == 0.0


@pytest.mark.asyncio
async def test_ask_retries_5xx_and_opens_breaker():
    def handler(request):
        return httpx.Response(503)
    c = RAGClient(
        base_url="http://x", max_retries=1,
        fail_threshold=2, cooldown_sec=10,
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(RAGUnavailable):
        await c.ask("q")  # 2 попытки → 1 failure (после всех ретраев)
    # После 1-го ask: 1 failure
    with pytest.raises(RAGUnavailable):
        await c.ask("q")  # ещё 1 failure → breaker открыт
    # Теперь breaker должен быть открыт
    assert c._breaker_open()


@pytest.mark.asyncio
async def test_ask_confidence_none_does_not_crash(caplog):
    """confidence=None не должен ронять логгер."""
    def handler(request):
        return httpx.Response(200, json={
            "answer": "x", "confidence": None, "sources": [],
        })
    c = RAGClient(base_url="http://x", transport=httpx.MockTransport(handler))
    with caplog.at_level("INFO"):
        answer = await c.ask("q")
    assert answer.confidence is None
    assert "confidence=n/a" in caplog.text


@pytest.mark.asyncio
async def test_ask_invalid_json_returns_rag_unavailable():
    """2xx с HTML → RAGUnavailable, не JSONDecodeError."""
    def handler(request):
        return httpx.Response(200, text="<html>error</html>")
    c = RAGClient(
        base_url="http://x", max_retries=0,
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(RAGUnavailable, match="rag_bad_response"):
        await c.ask("q")


def test_cache_key_non_serializable_returns_none():
    from datetime import datetime
    c = RAGClient(base_url="http://x")
    assert c._cache_key("q", {"when": datetime.now()}) is None


def test_base_url_whitespace_disabled():
    """'   ' → enabled=False, не '  /api/v1/ask'."""
    c = RAGClient(base_url="   ")
    assert c.enabled is False
    assert c.base_url == ""


@pytest.mark.asyncio
async def test_ingest_too_large_raises(tmp_path):
    big = tmp_path / "big.bin"
    big.write_bytes(b"x" * 100)
    c = RAGClient(base_url="http://x", max_ingest_bytes=10)
    with pytest.raises(RAGUnavailable, match="too_large"):
        await c.ingest(big, {})


@pytest.mark.asyncio
async def test_ingest_file_not_found(tmp_path):
    c = RAGClient(base_url="http://x")
    with pytest.raises(RAGUnavailable, match="file_error"):
        await c.ingest(tmp_path / "missing.bin", {})


def test_rag_client_from_settings_passes_breaker_config():
    """fail_threshold и cooldown_sec должны браться из Settings."""
    with patch("app.config.get_settings") as mock:
        mock.return_value = MagicMock(
            rag_service_url="http://x",
            rag_api_token=SecretStr("t"),
            rag_enabled=True,
            rag_timeout_sec=5,
            rag_cache_ttl_sec=3600,
            rag_cb_fail_threshold=10,
            rag_cb_cooldown_sec=600,
        )
        c = rag_client_from_settings()
    assert c._fail_threshold == 10
    assert c._cooldown_sec == 600


@pytest.mark.asyncio
async def test_cached_answer_latency_zeroed():
    """При кэш-хите latency_ms=0, а не оригинальная."""
    redis = AsyncMock()
    c = RAGClient(base_url="http://x", redis=redis)
    # 1-й вызов — сеть
    def handler(request):
        return httpx.Response(200, json={
            "answer": "x", "confidence": 0.9, "sources": [],
        })
    c._transport = httpx.MockTransport(handler)
    first = await c.ask("q")
    # MockTransport быстрый: int(ms) может дать 0 — важен не знак, а кэш.
    assert first.latency_ms >= 0
    # 2-й вызов — из кэша (эмулируем ответ redis)
    redis.get = AsyncMock(return_value=first.model_dump_json().encode())
    cached = await c.ask("q")
    assert cached.cached is True
    assert cached.latency_ms == 0  # не оригинальная