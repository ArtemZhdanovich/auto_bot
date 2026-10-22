"""DTO AI-поиска Mini App: вход сценария и параметры rate-limit."""

from __future__ import annotations

from dataclasses import dataclass

# Rate-limit на сообщения: защита бюджета от «бесплатных» LLM-вызовов.
AI_MESSAGE_RATE_LIMIT = 20
AI_MESSAGE_RATE_WINDOW_SEC = 60
AI_MESSAGE_RATE_PREFIX = "ai_msg_rl"


@dataclass(frozen=True, slots=True)
class AiMessageCommand:
    """Сообщение пользователю Mini App (пользователь — из JWT)."""

    user_id: int
    text: str
