"""LLM-клиенты (ТЗ п. 4.5): GLM (Zhipu) / Anthropic / Ollama через httpx.

Без тяжёлых SDK: GLM и Ollama используют совместимый /chat/completions,
Anthropic — /v1/messages. Transport-инъекция — для тестов.

Секреты приходят как `SecretStr` (см. config.py); клиент разворачивает
их через `_secret_or_none()` и никогда не кладёт в заголовки сырой `SecretStr`.
"""

import asyncio
import json
import logging
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

import httpx
from pydantic import SecretStr

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)

# Транзиентные статусы LLM-провайдеров, которые стоит ретраить.
_RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})
_BACKOFF_BASE_SEC = 1.0
_BACKOFF_CAP_SEC = 15.0
_MAX_RETRIES = 3


class LLMError(RuntimeError):
    """Ошибка вызова LLM или невалидный ответ."""


@dataclass
class ToolCall:
    """Вызов инструмента, возвращённый LLM."""

    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass
class LLMResult:
    """Результат вызова LLM."""

    text: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    # True, если finish_reason указывает на обрезку (length / content_filter).
    truncated: bool = False
    finish_reason: str | None = None


def _secret_or_none(value: SecretStr | str | None) -> str | None:
    """Разворачивает `SecretStr` в `str`; `None` для пустого значения.

    После миграции config.py на `SecretStr` прямая передача в заголовок
    даёт `Bearer **********`. Здесь — явная распаковка.

    Args:
        value: `SecretStr`, `str` или `None`.

    Returns:
        Строка секрета или `None`, если значение пустое.
    """
    if value is None:
        return None
    if isinstance(value, SecretStr):
        secret = value.get_secret_value()
        return secret or None
    return value or None


def _parse_tool_arguments(raw: Any) -> dict[str, Any]:
    """Приводит `function.arguments` к dict независимо от формата.

    Некоторые провайдеры возвращают `arguments` как dict, некоторые —
    как JSON-строку. `json.loads(dict)` падает с `TypeError`, поэтому
    различаем типы явно.

    Args:
        raw: Значение `arguments` (str / dict / list / None).

    Returns:
        Словарь аргументов (пустой для невалидного значения).

    Raises:
        LLMError: Если строка не парсится в JSON-объект.
    """
    if raw is None or raw == "":
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise LLMError(f"Invalid tool arguments JSON: {raw[:200]}") from exc
        if not isinstance(parsed, dict):
            raise LLMError(
                f"Tool arguments must be a JSON object, got {type(parsed).__name__}"
            )
        return parsed
    raise LLMError(f"Tool arguments must be str or dict, got {type(raw).__name__}")


def _backoff(attempt: int) -> float:
    """Экспоненциальный backoff с cap.

    Args:
        attempt: Номер попытки (0-based).

    Returns:
        Задержка в секундах.
    """
    raw = min(_BACKOFF_BASE_SEC * (2**attempt), _BACKOFF_CAP_SEC)
    return raw


class BaseLLMClient:
    """Базовый интерфейс LLM-клиента."""

    async def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None
    ) -> LLMResult:
        """Выполняет один вызов LLM.

        Args:
            messages: История сообщений в OpenAI-формате.
            tools: Список tools в формате `{"name", "description", "parameters"}`.

        Returns:
            `LLMResult` с текстом и/или tool_calls.

        Raises:
            LLMError: При сетевой ошибке, не-2xx или невалидном ответе.
        """
        raise NotImplementedError

    async def aclose(self) -> None:
        """Закрывает HTTP-клиент. Идемпотентно."""
        return None


class ChatCompletionsClient(BaseLLMClient):
    """GLM (Zhipu) / Ollama / DeepSeek — /chat/completions-совместимые."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        temperature: float = 0.2,
        timeout: int | float | httpx.Timeout = 60,
        max_retries: int = _MAX_RETRIES,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if max_retries < 1:
            raise ValueError("max_retries must be >= 1")
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=(
                timeout
                if isinstance(timeout, httpx.Timeout)
                else httpx.Timeout(float(timeout), connect=min(10.0, float(timeout)))
            ),
            follow_redirects=True,
            transport=transport,
        )
        self._model = model
        self._temperature = temperature
        self._max_retries = max_retries

    async def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None
    ) -> LLMResult:
        """Один вызов /chat/completions с ретраями.

        Args:
            messages: История сообщений.
            tools: Tools в формате `{"name", "description", "parameters"}`.

        Returns:
            `LLMResult`.

        Raises:
            LLMError: При исчерпании ретраев или невалидном ответе.
        """
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "temperature": self._temperature,
        }
        if tools:
            payload["tools"] = [
                {"type": "function", "function": t} for t in tools
            ]
            payload["tool_choice"] = "auto"

        resp = await self._post_with_retry("/chat/completions", payload)
        try:
            data = resp.json()
            message = data["choices"][0]["message"]
            finish_reason = data["choices"][0].get("finish_reason")
        except (KeyError, IndexError, ValueError) as exc:
            raise LLMError(
                f"Unexpected LLM response: {resp.text[:500]}"
            ) from exc

        # `content` может быть str или list (multimodal). Склеиваем текст.
        raw_content = message.get("content")
        if isinstance(raw_content, list):
            text = "".join(
                b.get("text", "")
                for b in raw_content
                if isinstance(b, dict) and b.get("type") == "text"
            ) or None
        elif isinstance(raw_content, str):
            text = raw_content
        else:
            text = None

        tool_calls: list[ToolCall] = []
        for tc in message.get("tool_calls") or []:
            if not isinstance(tc, dict):
                logger.warning("llm_skip_non_dict_tool_call: %r", type(tc).__name__)
                continue
            fn = tc.get("function")
            if not isinstance(fn, dict):
                logger.warning("llm_skip_tool_call_without_function: %r", tc)
                continue
            name = fn.get("name")
            if not isinstance(name, str) or not name:
                logger.warning("llm_skip_tool_call_without_name: %r", tc)
                continue
            try:
                arguments = _parse_tool_arguments(fn.get("arguments"))
            except LLMError:
                logger.exception("llm_invalid_tool_arguments: name=%s", name)
                raise
            tool_calls.append(
                ToolCall(
                    id=str(tc.get("id", "")),
                    name=name,
                    arguments=arguments,
                )
            )

        logger.info(
            "llm_call provider=chat model=%s finish_reason=%s "
            "has_text=%s tool_calls=%d",
            self._model, finish_reason, text is not None, len(tool_calls),
        )
        return LLMResult(
            text=text,
            tool_calls=tool_calls,
            truncated=finish_reason in ("length", "content_filter"),
            finish_reason=finish_reason,
        )

    async def _post_with_retry(
        self, path: str, payload: dict[str, Any]
    ) -> httpx.Response:
        """POST с ретраями на транзиентные ошибки.

        Args:
            path: Относительный путь.
            payload: JSON-тело.

        Returns:
            Успешный `httpx.Response`.

        Raises:
            LLMError: При исчерпании ретраев или не-транзиентной ошибке.
        """
        last_error: Exception | None = None
        for attempt in range(self._max_retries):
            try:
                resp = await self._client.post(path, json=payload)
            except httpx.TimeoutException as exc:
                last_error = exc
                delay = _backoff(attempt)
                logger.warning(
                    "llm_timeout attempt=%d/%d delay=%.1fs",
                    attempt + 1, self._max_retries, delay,
                )
                await asyncio.sleep(delay)
                continue
            except httpx.HTTPError as exc:
                last_error = exc
                logger.warning(
                    "llm_network_error attempt=%d/%d: %r",
                    attempt + 1, self._max_retries, exc,
                )
                await asyncio.sleep(_backoff(attempt))
                continue

            if resp.status_code in _RETRYABLE_STATUS:
                delay = _backoff(attempt)
                logger.warning(
                    "llm_retryable_status status=%d attempt=%d/%d delay=%.1fs",
                    resp.status_code, attempt + 1, self._max_retries, delay,
                )
                await asyncio.sleep(delay)
                continue

            try:
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise LLMError(
                    f"LLM request failed: HTTP {exc.response.status_code} "
                    f"for {exc.request.url}"
                ) from exc
            return resp

        raise LLMError(f"LLM request failed after {self._max_retries} attempts: {last_error}")

    async def aclose(self) -> None:
        """Закрывает HTTP-клиент."""
        await self._client.aclose()


class AnthropicClient(BaseLLMClient):
    """Claude через /v1/messages.

    ВНИМАНИЕ: multi-turn с tools не поддерживается в полном объёме.
    Сообщения с `role="tool"` (OpenAI-формат) не переводятся в
    Anthropic-формат `tool_result`. Если агент использует Anthropic
    с tools — нужен отдельный слой конвертации messages.

    Для полноценной поддержки: `messages` должны конвертироваться
    в Anthropic-формат (assistant с `tool_use` блоками, user с
    `tool_result` блоками) до вызова API.
    """

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        temperature: float = 0.2,
        timeout: int | float | httpx.Timeout = 60,
        max_tokens: int = 2048,
        base_url: str = "https://api.anthropic.com",
        max_retries: int = _MAX_RETRIES,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if max_retries < 1:
            raise ValueError("max_retries must be >= 1")
        if max_tokens <= 0:
            raise ValueError("max_tokens must be > 0")
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"},
            timeout=(
                timeout
                if isinstance(timeout, httpx.Timeout)
                else httpx.Timeout(float(timeout), connect=min(10.0, float(timeout)))
            ),
            follow_redirects=True,
            transport=transport,
        )
        self._model = model
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._max_retries = max_retries

    async def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None
    ) -> LLMResult:
        """Один вызов /v1/messages с ретраями.

        Args:
            messages: История в OpenAI-формате. `system` склеивается,
                `user`/`assistant` передаются, `tool` — отбрасываются
                (см. докстринг класса).
            tools: Tools в формате `{"name", "description", "parameters"}`.

        Returns:
            `LLMResult`.

        Raises:
            LLMError: При исчерпании ретраев или невалидном ответе.
        """
        system_parts: list[str] = []
        chat: list[dict[str, str]] = []
        dropped_tool_messages = 0
        for m in messages:
            role = m.get("role")
            content = m.get("content")
            if role == "system" and isinstance(content, str):
                system_parts.append(content)
            elif role in ("user", "assistant") and isinstance(content, str):
                chat.append({"role": role, "content": content})
            elif role == "tool":
                dropped_tool_messages += 1

        if dropped_tool_messages:
            logger.warning(
                "anthropic_dropped_tool_messages count=%d "
                "(multi-turn with tools not supported)",
                dropped_tool_messages,
            )

        payload: dict[str, Any] = {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "messages": chat,
            "temperature": self._temperature,
        }
        system = "\n".join(system_parts)
        if system:
            payload["system"] = system
        if tools:
            payload["tools"] = [
                {
                    "name": t["name"],
                    "description": t.get("description", ""),
                    "input_schema": t["parameters"],
                }
                for t in tools
            ]

        resp = await self._post_with_retry("/v1/messages", payload)
        try:
            data = resp.json()
        except ValueError as exc:
            raise LLMError(
                f"Unexpected LLM response (not JSON): {resp.text[:500]}"
            ) from exc

        result = LLMResult()
        text_parts: list[str] = []
        for block in data.get("content", []) or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                t = block.get("text")
                if isinstance(t, str):
                    text_parts.append(t)
            elif block.get("type") == "tool_use":
                name = block.get("name")
                if not isinstance(name, str) or not name:
                    logger.warning("anthropic_skip_tool_use_without_name: %r", block)
                    continue
                arguments = block.get("input", {})
                if not isinstance(arguments, dict):
                    raise LLMError(
                        f"Anthropic tool_use.input must be a dict, "
                        f"got {type(arguments).__name__}"
                    )
                result.tool_calls.append(
                    ToolCall(
                        id=str(block.get("id", "")),
                        name=name,
                        arguments=arguments,
                    )
                )
        result.text = "".join(text_parts) or None
        result.finish_reason = data.get("stop_reason")
        result.truncated = result.finish_reason in ("max_tokens",)

        logger.info(
            "llm_call provider=anthropic model=%s stop_reason=%s "
            "has_text=%s tool_calls=%d",
            self._model, result.finish_reason,
            result.text is not None, len(result.tool_calls),
        )
        return result

    async def _post_with_retry(
        self, path: str, payload: dict[str, Any]
    ) -> httpx.Response:
        """POST с ретраями на транзиентные ошибки."""
        last_error: Exception | None = None
        for attempt in range(self._max_retries):
            try:
                resp = await self._client.post(path, json=payload)
            except httpx.TimeoutException as exc:
                last_error = exc
                await asyncio.sleep(_backoff(attempt))
                continue
            except httpx.HTTPError as exc:
                last_error = exc
                await asyncio.sleep(_backoff(attempt))
                continue

            if resp.status_code in _RETRYABLE_STATUS:
                await asyncio.sleep(_backoff(attempt))
                continue

            try:
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise LLMError(
                    f"LLM request failed: HTTP {exc.response.status_code} "
                    f"for {exc.request.url}"
                ) from exc
            return resp

        raise LLMError(
            f"LLM request failed after {self._max_retries} attempts: {last_error}"
        )

    async def aclose(self) -> None:
        """Закрывает HTTP-клиент."""
        await self._client.aclose()


class MockLLMClient(BaseLLMClient):
    """Скриптованный клиент для тестов и dev без LLM."""

    def __init__(
        self,
        script: list[LLMResult] | None = None,
        *,
        raise_on_call: LLMError | None = None,
    ) -> None:
        self.script = list(script or [])
        self.calls: list[tuple[list[dict], list[dict] | None]] = []
        self._raise_on_call = raise_on_call

    async def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None
    ) -> LLMResult:
        """Возвращает следующий заскриптованный результат.

        Args:
            messages: История.
            tools: Tools.

        Returns:
            `LLMResult` из script или «Ok.».

        Raises:
            LLMError: Если `raise_on_call` задан.
        """
        self.calls.append((messages, tools))
        if self._raise_on_call is not None:
            raise self._raise_on_call
        if self.script:
            return self.script.pop(0)
        return LLMResult(text="Ok.")


def build_llm_client(
    settings: Settings, transport: httpx.AsyncBaseTransport | None = None
) -> BaseLLMClient:
    """Фабрика по settings.llm_provider: glm | anthropic | ollama | mock.

    Args:
        settings: Настройки приложения.
        transport: Инъекция транспорта для тестов.

    Returns:
        LLM-клиент.

    Raises:
        LLMError: Если провайдер неизвестен или не задан API-ключ.
    """
    provider = settings.llm_provider
    model = settings.llm_default_model
    temperature = settings.llm_temperature
    base_url = settings.llm_base_url

    if provider == "mock":
        return MockLLMClient()
    if provider == "anthropic":
        api_key = _secret_or_none(settings.anthropic_api_key)
        if not api_key:
            raise LLMError("ANTHROPIC_API_KEY is not set")
        return AnthropicClient(
            api_key=api_key,
            model=model,
            temperature=temperature,
            base_url=base_url or "https://api.anthropic.com",
            transport=transport,
        )
    if provider == "ollama":
        return ChatCompletionsClient(
            api_key="ollama",
            base_url=base_url or "http://localhost:11434/v1",
            model=model,
            temperature=temperature,
            transport=transport,
        )
    if provider == "glm":
        api_key = _secret_or_none(settings.glm_api_key)
        if not api_key:
            raise LLMError("GLM_API_KEY is not set")
        return ChatCompletionsClient(
            api_key=api_key,
            base_url=base_url or settings.glm_base_url,
            model=model,
            temperature=temperature,
            transport=transport,
        )
    raise LLMError(
        f"Unknown llm_provider: {provider!r} (glm | anthropic | ollama | mock)"
    )


_CACHED_CLIENTS: dict[tuple[str, str], BaseLLMClient] = {}


@lru_cache(maxsize=8)
def get_cached_llm_client(provider: str, model: str) -> BaseLLMClient:
    """Кэшированный LLM-клиент по паре `(provider, model)`.

    Клиент держит `httpx.AsyncClient` с пулом соединений: создавать его
    на каждый ход диалога — это TLS-handshake и утечка сокетов. Кэш на
    процесс; закрытие — `close_cached_llm_clients()` в lifespan.

    Args:
        provider: `glm` | `anthropic` | `ollama` | `mock`.
        model: Имя модели.

    Returns:
        `BaseLLMClient`.

    Raises:
        LLMError: Если провайдер неизвестен или не задан API-ключ.
    """
    settings = get_settings()
    if provider != settings.llm_provider or model != settings.llm_default_model:
        settings = settings.model_copy(
            update={"llm_provider": provider, "llm_default_model": model}
        )
    client = build_llm_client(settings)
    _CACHED_CLIENTS[(provider, model)] = client
    return client


async def close_cached_llm_clients() -> None:
    """Закрыть все кэшированные HTTP-клиенты (идемпотентно)."""
    for client in list(_CACHED_CLIENTS.values()):
        await client.aclose()
    _CACHED_CLIENTS.clear()
    get_cached_llm_client.cache_clear()