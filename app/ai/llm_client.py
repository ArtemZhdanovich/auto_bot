"""LLM-клиенты (ТЗ п. 4.5): OpenAI / Anthropic / Ollama через httpx.

Без тяжёлых SDK: OpenAI и Ollama используют совместимый /chat/completions,
Anthropic — /v1/messages. Transport-инъекция — для тестов.
"""

import json
import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.core.config import Settings

logger = logging.getLogger(__name__)


class LLMError(RuntimeError):
    """Ошибка вызова LLM или невалидный ответ."""


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass
class LLMResult:
    text: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)


class BaseLLMClient:
    async def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None
    ) -> LLMResult:
        raise NotImplementedError

    async def aclose(self) -> None:
        return None


class OpenAICompatibleClient(BaseLLMClient):
    """OpenAI / Ollama / любой /chat/completions-совместимый провайдер."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        temperature: float = 0.2,
        timeout: int = 60,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
            transport=transport,
        )
        self._model = model
        self._temperature = temperature

    async def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None
    ) -> LLMResult:
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "temperature": self._temperature,
        }
        if tools:
            payload["tools"] = [{"type": "function", "function": t} for t in tools]
            payload["tool_choice"] = "auto"

        try:
            resp = await self._client.post("/chat/completions", json=payload)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise LLMError(f"LLM request failed: {exc}") from exc

        try:
            message = resp.json()["choices"][0]["message"]
        except (KeyError, IndexError, ValueError) as exc:
            raise LLMError(f"Unexpected LLM response: {resp.text[:500]}") from exc

        tool_calls = []
        for tc in message.get("tool_calls") or []:
            try:
                arguments = json.loads(tc["function"].get("arguments") or "{}")
            except json.JSONDecodeError as exc:
                raise LLMError(f"Invalid tool arguments JSON: {tc}") from exc
            tool_calls.append(
                ToolCall(
                    id=tc.get("id", ""),
                    name=tc["function"]["name"],
                    arguments=arguments,
                )
            )
        return LLMResult(text=message.get("content"), tool_calls=tool_calls)

    async def aclose(self) -> None:
        await self._client.aclose()


class AnthropicClient(BaseLLMClient):
    """Claude через /v1/messages."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        temperature: float = 0.2,
        timeout: int = 60,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url="https://api.anthropic.com",
            headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"},
            timeout=timeout,
            transport=transport,
        )
        self._model = model
        self._temperature = temperature

    async def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None
    ) -> LLMResult:
        system = "\n".join(m["content"] for m in messages if m["role"] == "system")
        chat = [m for m in messages if m["role"] != "system"]
        payload: dict[str, Any] = {
            "model": self._model,
            "max_tokens": 1024,
            "system": system or None,
            "messages": [
                {"role": m["role"], "content": m["content"]}
                for m in chat
                if m["role"] in ("user", "assistant")
            ],
            "temperature": self._temperature,
        }
        if tools:
            payload["tools"] = [
                {
                    "name": t["name"],
                    "description": t.get("description", ""),
                    "input_schema": t["parameters"],
                }
                for t in tools
            ]

        try:
            resp = await self._client.post("/v1/messages", json=payload)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise LLMError(f"LLM request failed: {exc}") from exc

        result = LLMResult()
        for block in resp.json().get("content", []):
            if block.get("type") == "text":
                result.text = block.get("text")
            elif block.get("type") == "tool_use":
                result.tool_calls.append(
                    ToolCall(
                        id=block.get("id", ""),
                        name=block["name"],
                        arguments=block.get("input", {}),
                    )
                )
        return result

    async def aclose(self) -> None:
        await self._client.aclose()


class MockLLMClient(BaseLLMClient):
    """Скриптованный клиент для тестов и dev без LLM."""

    def __init__(self, script: list[LLMResult] | None = None) -> None:
        self.script = list(script or [])
        self.calls: list[tuple[list[dict], list[dict] | None]] = []

    async def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None
    ) -> LLMResult:
        self.calls.append((messages, tools))
        if self.script:
            return self.script.pop(0)
        return LLMResult(text="Ok.")


def build_llm_client(
    settings: Settings, transport: httpx.AsyncBaseTransport | None = None
) -> BaseLLMClient:
    """Фабрика по settings.llm_provider: openai | anthropic | ollama | mock."""
    provider = settings.llm_provider
    model = settings.llm_default_model
    temperature = settings.llm_temperature
    base_url = settings.llm_base_url

    if provider == "mock":
        return MockLLMClient()
    if provider == "anthropic":
        if not settings.anthropic_api_key:
            raise LLMError("ANTHROPIC_API_KEY is not set")
        return AnthropicClient(
            api_key=settings.anthropic_api_key,
            model=model,
            temperature=temperature,
            transport=transport,
        )
    if provider == "ollama":
        return OpenAICompatibleClient(
            api_key="ollama",
            base_url=base_url or "http://localhost:11434/v1",
            model=model,
            temperature=temperature,
            transport=transport,
        )
    # openai по умолчанию
    if not settings.openai_api_key:
        raise LLMError("OPENAI_API_KEY is not set")
    return OpenAICompatibleClient(
        api_key=settings.openai_api_key,
        base_url=base_url or "https://api.openai.com/v1",
        model=model,
        temperature=temperature,
        transport=transport,
    )
