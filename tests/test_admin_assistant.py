"""Тесты AI-помощника админа: tool-loop сервиса и эндпоинт /assistant/chat."""

import pytest
from fastapi.testclient import TestClient

from app.ai.llm_client import LLMResult, MockLLMClient, ToolCall
from app.core.config import get_settings
from app.main import app
from app.services import admin_assistant
from conftest import FakeSession

INTERNAL = {"X-Internal-Token": get_settings().app_secret_key}


# --- Сервис: assistant_reply --------------------------------------------------


@pytest.mark.asyncio
async def test_assistant_plain_answer_without_tools():
    llm = MockLLMClient([LLMResult(text="Сейчас 3 новых лида.")])
    reply = await admin_assistant.assistant_reply(FakeSession(), llm, "Лиды?")
    assert reply == "Сейчас 3 новых лида."


@pytest.mark.asyncio
async def test_assistant_tool_loop_returns_final_answer():
    s = FakeSession()
    s.set_result([("new", 5), ("claimed", 2)])  # результат leads_stats
    llm = MockLLMClient(
        [
            LLMResult(tool_calls=[ToolCall(id="t1", name="leads_stats", arguments={})]),
            LLMResult(text="Новых лидов: 5."),
        ]
    )
    reply = await admin_assistant.assistant_reply(s, llm, "Сколько новых лидов?")
    assert reply == "Новых лидов: 5."
    # второй вызов LLM получил результаты тулзы в контексте
    _, tools = llm.calls[0]
    assert tools and tools[0]["name"] == "leads_stats"
    second_messages = llm.calls[1][0]
    assert any(m.get("role") == "tool" for m in second_messages)


@pytest.mark.asyncio
async def test_assistant_unknown_tool_does_not_crash():
    llm = MockLLMClient(
        [
            LLMResult(tool_calls=[ToolCall(id="t1", name="drop_database", arguments={})]),
            LLMResult(text="Не могу это сделать."),
        ]
    )
    reply = await admin_assistant.assistant_reply(FakeSession(), llm, "удали всё")
    assert reply == "Не могу это сделать."


@pytest.mark.asyncio
async def test_assistant_tool_error_swallowed():
    class BrokenSession(FakeSession):
        async def execute(self, stmt):
            raise RuntimeError("db down")

    llm = MockLLMClient(
        [
            LLMResult(tool_calls=[ToolCall(id="t1", name="partners_list", arguments={})]),
            LLMResult(text="БД недоступна."),
        ]
    )
    reply = await admin_assistant.assistant_reply(BrokenSession(), llm, "партнёры?")
    assert reply == "БД недоступна."


# --- Эндпоинт: POST /api/admin/assistant/chat ---------------------------------


def test_assistant_requires_internal_token():
    client = TestClient(app)
    resp = client.post("/api/admin/assistant/chat", json={"message": "привет"})
    assert resp.status_code == 401


def test_assistant_llm_unavailable_503():
    """llm_provider=glm без GLM_API_KEY → LLMError → 503 (не падаем)."""
    client = TestClient(app)
    settings = get_settings()
    old_provider, old_key = settings.llm_provider, settings.glm_api_key
    settings.llm_provider, settings.glm_api_key = "glm", ""
    try:
        resp = client.post(
            "/api/admin/assistant/chat", json={"message": "лиды?"}, headers=INTERNAL
        )
        assert resp.status_code == 503
    finally:
        settings.llm_provider, settings.glm_api_key = old_provider, old_key


def test_assistant_chat_mock_provider():
    """llm_provider=mock → 200 и текстовый ответ (интеграционный smoke)."""
    client = TestClient(app)
    settings = get_settings()
    old_provider = settings.llm_provider
    settings.llm_provider = "mock"
    try:
        resp = client.post(
            "/api/admin/assistant/chat",
            json={"message": "привет", "history": [{"role": "user", "content": "хай"}]},
            headers=INTERNAL,
        )
        assert resp.status_code == 200
        assert resp.json()["reply"]
    finally:
        settings.llm_provider = old_provider