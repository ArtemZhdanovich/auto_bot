"""Agent loop (ТЗ п. 7.2): LLM — диспетчер с tools, состояние живёт отдельно."""

import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.llm_client import BaseLLMClient, LLMError, ToolCall
from app.ai.prompts import build_messages, state_summary
from app.ai.rag_client import RAGClient, RAGUnavailable
from app.ai.tools import (
    TOOLS,
    ToolOutcome,
    apply_patch,
    has_critical_fields,
    lookup_reference,
)
from app.schemas.criteria import SearchCriteria
from app.schemas.dialog import DialogState, Message
from app.services.dialog import DialogStore, answer_faq, match_faq_topic, new_state
from app.services.search import preview_listings

logger = logging.getLogger(__name__)

_RAG_TOPICS = {"customs", "delivery", "risks", "fees", "faq"}


def _last_user_question(state: Any) -> str:
    """Последнее сообщение пользователя — вопрос для RAG, если LLM не передал свой."""
    for msg in reversed(state.messages):
        if msg.role == "user":
            return msg.content
    return ""


async def _ask_rag(
    topic: str, question: str, state: Any, rag: RAGClient | None
) -> str | None:
    """Запрос в RAG-сервис (план внедрения RAG, п. 8.1).

    Возвращает текст ответа или None (RAG выключен/недоступен/низкий
    confidence → вызывающий код уходит в fallback на LLM).
    """
    if rag is None or not rag.enabled or not question:
        return None
    context = {
        "tenant": "autoselect",
        "user_id": state.user_id,
        "filters": {"topic": topic} if topic in _RAG_TOPICS else {},
    }
    from app.core.config import get_settings

    threshold = get_settings().rag_confidence_threshold
    try:
        answer = await rag.ask(question, context)
    except RAGUnavailable as exc:
        logger.warning("rag_unavailable: reason=%s user_id=%s", exc, state.user_id)
        return None
    if answer.confidence < threshold:
        logger.info(
            "rag_low_confidence: %.2f < %.2f user_id=%s",
            answer.confidence, threshold, state.user_id,
        )
        return None
    return answer.answer


class DialogError(RuntimeError):
    """LLM недоступна / ответ невалиден → fallback на кнопки (ТЗ п. 7.9)."""


@dataclass
class ToolContext:
    session: AsyncSession
    redis: Redis
    finalize: Callable[[SearchCriteria], Awaitable[list[str]]] | None = None
    rag: RAGClient | None = None


@dataclass
class AgentReply:
    session_id: str
    text: str
    options: list[str] = field(default_factory=list)
    status: str = "collecting"
    finalize_ready: bool = False
    turn_count: int = 0
    criteria: dict[str, Any] = field(default_factory=dict)


async def _execute_tool(
    tc: ToolCall, state: DialogState, ctx: ToolContext
) -> ToolOutcome:
    """Валидация результата и обновление DialogState (п. 7.2)."""
    args = tc.arguments

    if tc.name == "update_criteria":
        applied = apply_patch(state, args)
        if applied:
            logger.info("tool_update_criteria: fields=%s", applied)
            return ToolOutcome(text=f"Записал: {state_summary(state)}.")
        return ToolOutcome(text="Не понял параметры — уточните, пожалуйста.")

    if tc.name == "ask_clarification":
        question = str(args.get("question", "Уточните, пожалуйста:"))
        field_name = str(args.get("field", ""))
        if field_name and field_name not in state.asked_fields:
            state.asked_fields.append(field_name)
        options = [str(o) for o in args.get("options", [])]
        return ToolOutcome(text=question, options=options, asked_field=field_name)

    if tc.name == "answer_question":
        topic = str(args.get("topic", ""))
        state.answered_questions.append(topic)
        question = str(args.get("question", "")) or _last_user_question(state)
        llm_answer = str(args.get("answer", "")).strip() or None

        # 1. RAG: фактические вопросы — из базы знаний (план внедрения RAG, фаза 4)
        rag_answer_text = await _ask_rag(topic, question, state, ctx.rag)
        if rag_answer_text:
            return ToolOutcome(text=rag_answer_text)

        # 2. Fallback: ответ LLM без контекста (план п. 8.1, шаг 3)
        if llm_answer:
            return ToolOutcome(text=llm_answer)
        return ToolOutcome(
            text="Точного ответа у меня сейчас нет — не хочу вводить неверными "
            "цифрами. Рекомендую уточнить у менеджера, он подскажет точно."
        )

    if tc.name == "lookup_reference":
        found = lookup_reference(str(args.get("query", "")))
        text = found or "В справочнике ничего не нашёл. Назовите марку и модель точнее."
        return ToolOutcome(text=text)

    if tc.name == "preview_search":
        criteria = state.criteria
        patch = args.get("criteria")
        if isinstance(patch, dict):
            apply_patch(state, patch)
            criteria = state.criteria
        limit = int(args.get("limit", 3))
        items = await preview_listings(ctx.session, criteria, limit=limit)
        if not items:
            return ToolOutcome(text="Пока ничего не нашлось по этим критериям.")
        bullets = "\n".join(
            f"• {i.make or '?'} {i.model or ''} {i.year or ''}, "
            f"{i.price or '?'} {i.currency or ''}, {i.mileage_km or '?'} км"
            for i in items
        )
        return ToolOutcome(text=f"Вот что есть сейчас:\n{bullets}")

    if tc.name == "calc_delivery":
        from app.services.delivery import calc_delivery, format_breakdown

        price = float(args.get("price_usd") or 0)
        year = int(args.get("year") or 0)
        cc = int(args.get("engine_cc") or 2000)
        if price <= 0 or year < 1980:
            return ToolOutcome(text="Нужны цена в $ и год выпуска — уточните, пожалуйста.")
        breakdown = calc_delivery(price, cc, year)
        return ToolOutcome(text=format_breakdown(breakdown))

    if tc.name == "finalize_search":
        patch = args.get("criteria")
        if isinstance(patch, dict):
            apply_patch(state, patch)
        state.status = "searching"
        if ctx.finalize:
            await ctx.finalize(state.criteria)
        logger.info("tool_finalize_search: user_id=%s", state.user_id)
        return ToolOutcome(
            text="Запускаю поиск по источникам. Пришлю уведомления о новых вариантах.",
            finalized=True,
        )

    logger.warning("unknown_tool: %s", tc.name)
    return ToolOutcome(text="Не понял, что сделать. Переформулируйте, пожалуйста.")


def _to_reply(state: DialogState, text: str, options: list[str]) -> AgentReply:
    ready = state.status != "searching" and has_critical_fields(state.criteria)
    return AgentReply(
        session_id=state.session_id,
        text=text,
        options=options,
        status=state.status,
        finalize_ready=ready,
        turn_count=state.turn_count,
        criteria=state.criteria.model_dump(mode="json"),
    )


async def run_agent(
    user_id: int,
    text: str,
    *,
    session: AsyncSession,
    redis: Redis,
    llm: BaseLLMClient,
    finalize: Callable[[SearchCriteria], Awaitable[list[str]]] | None = None,
    store: DialogStore | None = None,
    rag: RAGClient | None = None,
) -> AgentReply:
    """Один ход диалога (п. 7.2). При ошибке LLM — DialogError → кнопки (п. 7.9)."""
    from app.core.config import get_settings

    settings = get_settings()
    store = store or DialogStore(redis)
    state = await store.load(user_id) or new_state(user_id)

    # 1. FAQ без LLM (п. 7.8) — только на «вопросные» сообщения
    topic = match_faq_topic(text)
    if topic:
        answer = await answer_faq(redis, topic)
        if answer:
            state.messages.append(Message(role="user", content=text))
            state.messages.append(Message(role="assistant", content=answer))
            state.turn_count += 1
            await store.save(state)
            return _to_reply(state, answer, options=[])

    # 2. сообщение пользователя в историю
    state.messages.append(Message(role="user", content=text))
    state.turn_count += 1

    # 3. ранний выход: все критичные поля есть — не тратим LLM (п. 7.7.6)
    if has_critical_fields(state.criteria) and state.status == "collecting":
        state.status = "ready"
        await store.save(state)
        return _to_reply(
            state,
            f"Всё главное собрал: {state_summary(state)}. Запускаем поиск?",
            options=["🔍 Искать"],
        )

    # 4. LLM + tools + история (последние N сообщений)
    try:
        result = await llm.complete(
            build_messages(state, settings.dialog_history_len), TOOLS
        )
    except LLMError as exc:
        raise DialogError(str(exc)) from exc

    # 5. выполнение tools (валидация каждого патча — п. 7.1)
    if rag is None and settings.rag_service_url and settings.rag_enabled:
        from app.ai.rag_client import rag_client_from_settings

        rag = rag_client_from_settings(redis)
    ctx = ToolContext(session=session, redis=redis, finalize=finalize, rag=rag)
    reply_text: str | None = result.text
    options: list[str] = []
    for tc in result.tool_calls:
        try:
            outcome = await _execute_tool(tc, state, ctx)
        except Exception:
            logger.exception("tool_failed: %s", tc.name)
            reply_text = reply_text or "Что-то пошло не так. Попробуйте ещё раз."
            continue
        if outcome.text:
            reply_text = outcome.text
        if outcome.options:
            options = outcome.options

    if state.status == "searching":
        options = []

    await store.save(state)
    return _to_reply(state, reply_text or "Продолжим — что ещё уточнить?", options)
