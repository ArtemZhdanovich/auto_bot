"""Тесты LLM-клиентов (парсинг tool_calls, ошибки)."""

import json

import httpx
import pytest

from app.ai.llm_client import (
    AnthropicClient,
    LLMError,
    MockLLMClient,
    OpenAICompatibleClient,
    ToolCall,
    LLMResult,
)


def _openai_transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer test-key"
        payload = json.loads(request.content)
        assert payload["model"] == "gpt-test"
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
async def test_openai_parses_tool_calls():
    client = OpenAICompatibleClient(
        api_key="test-key",
        base_url="https://api.openai.com/v1",
        model="gpt-test",
        transport=_openai_transport(),
    )
    result = await client.complete([{"role": "user", "content": "hi"}], tools=[{"name": "update_criteria"}])
    assert result.text is None
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].name == "update_criteria"
    assert result.tool_calls[0].arguments == {"make": "Kia"}
    await client.aclose()


@pytest.mark.asyncio
async def test_openai_invalid_json_args_raises():
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

    client = OpenAICompatibleClient(
        api_key="k",
        base_url="https://x/v1",
        model="m",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(LLMError):
        await client.complete([{"role": "user", "content": "hi"}])
    await client.aclose()


@pytest.mark.asyncio
async def test_openai_http_error_raises_llm_error():
    client = OpenAICompatibleClient(
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
    from app.ai.llm_client import build_llm_client
    from app.core.config import Settings

    settings = Settings(
        llm_provider="glm", glm_api_key="glm-key", _env_file=None
    )
    client = build_llm_client(settings)
    try:
        assert type(client) is OpenAICompatibleClient
        assert client._model == "glm-4.6"
        assert (
            str(client._client.base_url).rstrip("/")
            == "https://api.z.ai/api/paas/v4"
        )
        assert client._client.headers["Authorization"] == "Bearer glm-key"
    finally:
        await client.aclose()


def test_build_llm_client_glm_missing_key_raises():
    from app.ai.llm_client import build_llm_client
    from app.core.config import Settings

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
