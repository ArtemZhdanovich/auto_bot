"""Тонкий фасад AI-помощника админа: сборка портов и ответ модели."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.application.admin.assistant import (
    ADMIN_TOOLS,
    SYSTEM_PROMPT,
    AdminAssistant,
)
from app.application.ports import LLMPort
from app.infrastructure.db.repositories.admin_stats import (
    SqlAlchemyAdminStatsRepository,
)
from app.infrastructure.db.repositories.parser_health import (
    SqlAlchemySourceHealthRepository,
)

__all__ = ["ADMIN_TOOLS", "SYSTEM_PROMPT", "assistant_reply"]


async def assistant_reply(
    session: AsyncSession,
    llm: LLMPort,
    message: str,
    history: list[dict[str, str]] | None = None,
) -> str:
    """Один ход ассистента: LLM + tools (до 3 итераций) → текстовый ответ."""
    assistant = AdminAssistant(
        llm,
        SqlAlchemyAdminStatsRepository(session),
        SqlAlchemySourceHealthRepository(session),
    )
    return await assistant.reply(message, history)
