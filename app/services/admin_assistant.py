"""AI-помощник админа (UI/UX ТЗ §3.2, фаза 4).

LLM с tools поверх данных админ-панели: статистика лидов, партнёры,
здоровье источников. Только чтение — админ-действия остаются за человеком.
Инструменты выполняются на backend, модель получает компактные JSON-ответы.
"""

import logging
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.llm_client import BaseLLMClient, ToolCall
from app.db.models import Lead, PartnerAccount, Source, SourceHealth

logger = logging.getLogger(__name__)

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


async def _tool_leads_stats(session: AsyncSession) -> dict[str, Any]:
    rows = (
        await session.execute(
            select(Lead.status, func.count()).group_by(Lead.status)
        )
    ).all()
    by_status = {status: int(n) for status, n in rows}
    feedback = (
        await session.execute(
            select(Lead.partner_status, func.count()).group_by(Lead.partner_status)
        )
    ).all()
    by_feedback = {str(k): int(n) for k, n in feedback if k is not None}
    return {"leads_by_status": by_status, "partner_feedback": by_feedback}


async def _tool_partners_list(session: AsyncSession) -> list[dict[str, Any]]:
    accounts = (await session.execute(select(PartnerAccount))).scalars().all()
    return [
        {
            "id": a.id,
            "name": a.name,
            "plan": a.plan,
            "daily_quota": a.daily_quota,
            "status": a.status,
        }
        for a in accounts
    ]


async def _tool_sources_health(session: AsyncSession) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            select(Source.name, Source.enabled, SourceHealth.error_streak,
                   SourceHealth.last_error, SourceHealth.avg_latency_ms,
                   SourceHealth.total_fetched)
            .outerjoin(SourceHealth, SourceHealth.source_name == Source.name)
            .order_by(Source.priority)
        )
    ).all()
    return [
        {
            "name": name,
            "enabled": enabled,
            "error_streak": streak or 0,
            "last_error": last_error,
            "avg_latency_ms": avg_latency,
            "total_fetched": total,
        }
        for name, enabled, streak, last_error, avg_latency, total in rows
    ]


async def _execute_tool(session: AsyncSession, tc: ToolCall) -> str:
    import json

    try:
        if tc.name == "leads_stats":
            return json.dumps(await _tool_leads_stats(session), ensure_ascii=False)
        if tc.name == "partners_list":
            return json.dumps(await _tool_partners_list(session), ensure_ascii=False)
        if tc.name == "sources_health":
            return json.dumps(await _tool_sources_health(session), ensure_ascii=False)
    except Exception as exc:  # noqa: BLE001 — ошибка тулзы уходит модели как текст
        logger.error("admin_assistant_tool_failed: %s %s", tc.name, exc)
        return json.dumps({"error": str(exc)})
    logger.warning("admin_assistant_unknown_tool: %s", tc.name)
    return json.dumps({"error": f"unknown tool {tc.name}"})


async def assistant_reply(
    session: AsyncSession,
    llm: BaseLLMClient,
    message: str,
    history: list[dict[str, str]] | None = None,
) -> str:
    """Один ход ассистента: LLM + tools (до 3 итераций) → текстовый ответ."""
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        *(history or [])[-8:],  # ограничение контекста
        {"role": "user", "content": message},
    ]
    for _ in range(3):
        result = await llm.complete(messages, ADMIN_TOOLS)
        if result.text and not result.tool_calls:
            return result.text
        if result.tool_calls:
            tool_results = []
            for tc in result.tool_calls:
                output = await _execute_tool(session, tc)
                tool_results.append({"name": tc.name, "output": output})
            messages.append(
                {
                    "role": "assistant",
                    "content": result.text or "",
                    "tool_calls": [
                        {"name": tc.name, "arguments": tc.arguments}
                        for tc in result.tool_calls
                    ],
                }
            )
            for tr in tool_results:
                messages.append(
                    {"role": "tool", "name": tr["name"], "content": tr["output"]}
                )
            continue
        if result.text:
            return result.text
    return "Не удалось собрать ответ — попробуйте переформулировать вопрос."
