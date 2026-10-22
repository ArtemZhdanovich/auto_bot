"""Тесты LLM-клиентов (парсинг tool_calls, ошибки)."""

import asyncio
import json

import httpx
import pytest

from pydantic import SecretStr

from app.infrastructure.ai.llm_client import (
    AnthropicClient,
    ChatCompletionsClient,
    LLMError,
    MockLLMClient,
    ToolCall,
    LLMResult,
    _parse_tool_arguments,
    _secret_or_none,
    build_llm_client,
)
from app.config import Settings


def _settings(**overrides):
    """Settings с переопределёнными полями (без чтения .env).

    `provider`/`model` — короткие имена для `llm_provider`/`llm_default_model`.
    """
    mapping = {"provider": "llm_provider", "model": "llm_default_model"}
    data = {mapping.get(k, k): v for k, v in overrides.items()}
    return Settings(_env_file=None).model_copy(update=data)


def _chat_completions_transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer test-key"
        payload = json.loads(request.content)
        assert payload["model"] == "glm-test"
        assert payload["tools"][0]["function"]["name"] == "update_criteria"
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "function": {
                                        "name": "update_criteria",
                                        "arguments": json.dumps({"make": "Kia"}),
                                    },
                                }
                            ],
                        }
                    }
                ]
            },
        )

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_chat_completions_parses_tool_calls():
    client = ChatCompletionsClient(
        api_key="test-key",
        base_url="https://api.z.ai/api/paas/v4",
        model="glm-test",
        transport=_chat_completions_transport(),
    )
    result = await client.complete([{"role": "user", "content": "hi"}], tools=[{"name": "update_criteria"}])
    assert result.text is None
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].name == "update_criteria"
    assert result.tool_calls[0].arguments == {"make": "Kia"}
    await client.aclose()


@pytest.mark.asyncio
async def test_chat_completions_invalid_json_args_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                {"id": "c1", "function": {"name": "x", "arguments": "{broken"}}
                            ]
                        }
                    }
                ]
            },
        )

    client = ChatCompletionsClient(
        api_key="k",
        base_url="https://x/v1",
        model="m",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(LLMError):
        await client.complete([{"role": "user", "content": "hi"}])
    await client.aclose()


@pytest.mark.asyncio
async def test_chat_completions_http_error_raises_llm_error():
    client = ChatCompletionsClient(
        api_key="k",
        base_url="https://x/v1",
        model="m",
        transport=httpx.MockTransport(lambda r: httpx.Response(500)),
    )
    with pytest.raises(LLMError):
        await client.complete([{"role": "user", "content": "hi"}])
    await client.aclose()


@pytest.mark.asyncio
async def test_anthropic_parses_tool_use():
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["tools"][0]["input_schema"]["type"] == "object"
        assert payload["messages"][0]["role"] == "user"
        return httpx.Response(
            200,
            json={
                "content": [
                    {"type": "text", "text": "Смотрю"},
                    {
                        "type": "tool_use",
                        "id": "tu_1",
                        "name": "ask_clarification",
                        "input": {"field": "make", "question": "Какая марка?"},
                    },
                ]
            },
        )

    client = AnthropicClient(
        api_key="k", model="claude-test", transport=httpx.MockTransport(handler)
    )
    result = await client.complete(
        [{"role": "system", "content": "sys"}, {"role": "user", "content": "привет"}],
        tools=[{"name": "ask_clarification", "parameters": {"type": "object"}}],
    )
    assert result.text == "Смотрю"
    assert result.tool_calls[0].name == "ask_clarification"
    assert result.tool_calls[0].arguments == {"field": "make", "question": "Какая марка?"}
    await client.aclose()


@pytest.mark.asyncio
async def test_build_llm_client_glm_default():
    """GLM — основной провайдер: base_url z.ai, модель glm-4.6, Bearer-ключ."""
    from app.infrastructure.ai.llm_client import build_llm_client
    from app.config import Settings

    settings = Settings(
        llm_provider="glm", glm_api_key="glm-key", _env_file=None
    )
    client = build_llm_client(settings)
    try:
        assert type(client) is ChatCompletionsClient
        assert client._model == "glm-4.6"
        assert (
            str(client._client.base_url).rstrip("/")
            == "https://api.z.ai/api/paas/v4"
        )
        assert client._client.headers["Authorization"] == "Bearer glm-key"
    finally:
        await client.aclose()


def test_build_llm_client_glm_missing_key_raises():
    from app.infrastructure.ai.llm_client import build_llm_client
    from app.config import Settings

    settings = Settings(llm_provider="glm", _env_file=None)
    with pytest.raises(LLMError, match="GLM_API_KEY"):
        build_llm_client(settings)


@pytest.mark.asyncio
async def test_mock_client_script():
    client = MockLLMClient(script=[LLMResult(text="Ответ"), LLMResult(tool_calls=[ToolCall(id="1", name="update_criteria", arguments={})])])
    first = await client.complete([])
    second = await client.complete([])
    third = await client.complete([])
    assert first.text == "Ответ"
    assert second.tool_calls[0].name == "update_criteria"
    assert third.text == "Ok."  # script исчерпан
    await client.aclose()



# tests/ai/test_llm_client.py
import pytest



# --- SecretStr unwrap -------------------------------------------------

def test_secret_or_none_unwraps_secretstr():
    assert _secret_or_none(SecretStr("abc")) == "abc"
    assert _secret_or_none("plain") == "plain"
    assert _secret_or_none(None) is None
    assert _secret_or_none(SecretStr("")) is None


def test_build_llm_client_with_secretstr_key(monkeypatch):
    """Регрессия: SecretStr не должен уходить в Authorization как '**********'."""
    settings = _settings(provider="glm", glm_api_key=SecretStr("real-key"))
    client = build_llm_client(settings)
    # Проверяем, что заголовок содержит реальный ключ, не маску.
    assert "real-key" in client._client.headers["authorization"]
    assert "**********" not in client._client.headers["authorization"]


def test_build_llm_client_empty_secret_raises():
    settings = _settings(provider="glm", glm_api_key=SecretStr(""))
    with pytest.raises(LLMError, match="GLM_API_KEY is not set"):
        build_llm_client(settings)


# --- _parse_tool_arguments --------------------------------------------

def test_parse_arguments_from_dict():
    assert _parse_tool_arguments({"a": 1}) == {"a": 1}


def test_parse_arguments_from_json_string():
    assert _parse_tool_arguments('{"a": 1}') == {"a": 1}


def test_parse_arguments_null_returns_empty():
    assert _parse_tool_arguments(None) == {}
    assert _parse_tool_arguments("") == {}


def test_parse_arguments_invalid_json_raises():
    with pytest.raises(LLMError, match="Invalid tool arguments"):
        _parse_tool_arguments("not-json")


def test_parse_arguments_non_object_json_raises():
    with pytest.raises(LLMError, match="must be a JSON object"):
        _parse_tool_arguments("[1,2,3]")


def test_parse_arguments_unsupported_type_raises():
    with pytest.raises(LLMError, match="must be str or dict"):
        _parse_tool_arguments(42)


# --- ChatCompletionsClient --------------------------------------------

def test_chat_tool_call_with_dict_arguments():
    """Провайдер вернул arguments как dict — не json.loads(dict)."""
    import httpx
    def handler(request):
        return httpx.Response(200, json={
            "choices": [{"message": {
                "content": None,
                "tool_calls": [{
                    "id": "1",
                    "function": {"name": "f", "arguments": {"x": 1}},
                }],
            }, "finish_reason": "tool_calls"}],
        })
    c = ChatCompletionsClient(
        api_key="k", base_url="http://x", model="m",
        transport=httpx.MockTransport(handler),
    )
    result = asyncio.run(c.complete([{"role": "user", "content": "hi"}]))
    assert result.tool_calls[0].arguments == {"x": 1}


def test_chat_retries_on_429():
    import httpx
    calls = {"n": 0}
    def handler(request):
        calls["n"] += 1
        if calls["n"] < 2:
            return httpx.Response(429)
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
        })
    c = ChatCompletionsClient(
        api_key="k", base_url="http://x", model="m",
        transport=httpx.MockTransport(handler),
    )
    result = asyncio.run(c.complete([{"role": "user", "content": "hi"}]))
    assert result.text == "ok"
    assert calls["n"] == 2


def test_chat_multimodal_content_joined():
    """content как list[{"type":"text",...}] — склеиваем в строку."""
    import httpx
    def handler(request):
        return httpx.Response(200, json={
            "choices": [{"message": {"content": [
                {"type": "text", "text": "Hello "},
                {"type": "text", "text": "world"},
            ]}, "finish_reason": "stop"}],
        })
    c = ChatCompletionsClient(
        api_key="k", base_url="http://x", model="m",
        transport=httpx.MockTransport(handler),
    )
    result = asyncio.run(c.complete([{"role": "user", "content": "hi"}]))
    assert result.text == "Hello world"


def test_chat_truncated_flag_set():
    import httpx
    def handler(request):
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "..."}, "finish_reason": "length"}],
        })
    c = ChatCompletionsClient(
        api_key="k", base_url="http://x", model="m",
        transport=httpx.MockTransport(handler),
    )
    result = asyncio.run(c.complete([{"role": "user", "content": "hi"}]))
    assert result.truncated is True


# --- AnthropicClient --------------------------------------------------

def test_anthropic_tool_use_dropped_for_multiturn():
    """tool-role сообщения логируются как dropped, но не ломают вызов."""
    import httpx
    def handler(request):
        return httpx.Response(200, json={
            "content": [{"type": "text", "text": "ok"}],
            "stop_reason": "end_turn",
        })
    c = AnthropicClient(
        api_key="k", model="m",
        transport=httpx.MockTransport(handler),
    )
    messages = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "calling"},
        {"role": "tool", "content": "result", "tool_call_id": "1"},
    ]
    result = asyncio.run(c.complete(messages))
    assert result.text == "ok"


def test_anthropic_invalid_json_raises_llm_error():
    """2xx с не-JSON — LLMError, а не JSONDecodeError."""
    import httpx
    def handler(request):
        return httpx.Response(200, text="<html>error</html>")
    c = AnthropicClient(
        api_key="k", model="m",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(LLMError, match="not JSON"):
        asyncio.run(c.complete([{"role": "user", "content": "hi"}]))


def test_anthropic_system_omitted_when_empty():
    """system не передаётся как None — ключ вообще отсутствует."""
    import httpx
    captured = {}
    def handler(request):
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={
            "content": [{"type": "text", "text": "ok"}],
            "stop_reason": "end_turn",
        })
    c = AnthropicClient(
        api_key="k", model="m",
        transport=httpx.MockTransport(handler),
    )
    asyncio.run(c.complete([{"role": "user", "content": "hi"}]))
    assert "system" not in captured["body"]


# --- MockLLMClient ---------------------------------------------------

def test_mock_raise_on_call():
    c = MockLLMClient(raise_on_call=LLMError("boom"))
    with pytest.raises(LLMError, match="boom"):
        asyncio.run(c.complete([{"role": "user", "content": "hi"}]))