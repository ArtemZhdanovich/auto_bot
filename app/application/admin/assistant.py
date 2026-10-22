"""AI-помощник админа (UI/UX ТЗ §3.2, фаза 4): tool-loop поверх портов.

LLM с tools отвечает по данным админ-панели: статистика лидов, партнёры,
здоровье источников. Только чтение — админ-действия остаются за человеком.
Инструменты выполняются на backend, модель получает компактные JSON-ответы.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.application.admin.ports import AdminStatsPort, SourceHealthPort
from app.application.shared.ports import LLMPort

logger = logging.getLogger(__name__)

MAX_STEPS = 3  # сколько раз даём модели позвать инструменты
HISTORY_LIMIT = 8  # сколько прошлых сообщений держим в контексте
FALLBACK = "Не удалось собрать ответ — попробуйте переформулировать вопрос."

SYSTEM_PROMPT = (
    "Ты — AI-помощник администратора платформы подбора авто из-за рубежа. "
    "Отвечай кратко и по делу на русском. У тебя есть инструменты для "
    "просмотра статистики лидов, списка партнёров и здоровья источников — "
    "вызывай их, прежде чем отвечать на вопросы о текущих данных. "
    "Ничего не выдумывай: если данных нет — так и скажи. "
    "Админ-действия (создание/ротация ключей, отключение источников) ты "
    "не выполняешь — подскажи, где это сделать в панели."
)

ADMIN_TOOLS: list[dict[str, Any]] = [
    {
        "name": "leads_stats",
        "description": "Статистика лидов: количество по статусам и партнёрские фидбеки.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "partners_list",
        "description": "Список партнёров: план, дневная квота, сколько лидов забрали сегодня.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "sources_health",
        "description": "Источники данных и их здоровье: ошибки подряд, латентность, объём.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
]


class AdminAssistant:
    """Один ход ассистента: LLM + tools (до `MAX_STEPS` итераций)."""

    def __init__(
        self,
        llm: LLMPort,
        stats: AdminStatsPort,
        health: SourceHealthPort,
    ) -> None:
        self._llm = llm
        self._stats = stats
        self._health = health

    async def reply(
        self, message: str, history: list[dict[str, str]] | None = None
    ) -> str:
        """Ответить на вопрос админа, при необходимости вызвав инструменты.

        Args:
            message: Вопрос администратора.
            history: Прошлые сообщения чата (`role`/`content`).

        Returns:
            Текстовый ответ ассистента.
        """
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            *(history or [])[-HISTORY_LIMIT:],
            {"role": "user", "content": message},
        ]
        for _ in range(MAX_STEPS):
            result = await self._llm.complete(messages, ADMIN_TOOLS)
            text = getattr(result, "text", None)
            tool_calls = list(getattr(result, "tool_calls", None) or ())
            if text and not tool_calls:
                return text
            if not tool_calls:
                if text:
                    return text
                continue
            messages.append(
                {
                    "role": "assistant",
                    "content": text or "",
                    "tool_calls": [
                        {
                            "name": getattr(call, "name", ""),
                            "arguments": getattr(call, "arguments", {}),
                        }
                        for call in tool_calls
                    ],
                }
            )
            for call in tool_calls:
                name = getattr(call, "name", "")
                messages.append(
                    {
                        "role": "tool",
                        "name": name,
                        "content": await self._run_tool(name),
                    }
                )
        return FALLBACK

    async def _run_tool(self, name: str) -> str:
        """Выполнить инструмент; любая ошибка уходит модели как текст."""
        try:
            if name == "leads_stats":
                data: Any = {
                    "leads_by_status": await self._stats.leads_by_status(),
                    "partner_feedback": await self._stats.partner_feedback(),
                }
            elif name == "partners_list":
                data = await self._stats.partners()
            elif name == "sources_health":
                data = await self._health.list_with_health()
            else:
                logger.warning("admin_assistant_unknown_tool: %s", name)
                return json.dumps({"error": f"unknown tool {name}"})
        except Exception as exc:  # noqa: BLE001 — ошибка тулзы уходит модели
            logger.error("admin_assistant_tool_failed: %s %s", name, exc)
            return json.dumps({"error": str(exc)})
        return json.dumps(data, ensure_ascii=False)
