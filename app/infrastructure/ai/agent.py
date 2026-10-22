"""Agent loop (ТЗ п. 7.2): LLM — диспетчер с tools, состояние живёт отдельно."""

import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.ai.llm_client import BaseLLMClient, LLMError, ToolCall
from app.infrastructure.ai.prompts import build_messages, state_summary
from app.infrastructure.ai.rag_client import RAGClient, RAGUnavailable
from app.infrastructure.ai.tools import (
    FIELD_OPTIONS,
    TOOLS,
    ToolOutcome,
    apply_patch,
    has_critical_fields,
    lookup_reference,
)
from app.domain.search.criteria import SearchCriteria
from app.domain.dialog.state import DialogState, Message
from app.infrastructure.services.ai_quota import quota_allow, quota_consume
from app.infrastructure.services.analytics import EVENT_AI_SEARCH, track
from app.domain.dialog.faq import match_faq_topic
from app.infrastructure.services.dialog import DialogStore, answer_faq, new_state
from app.infrastructure.services.search import search_with_relaxation

logger = logging.getLogger(__name__)

_RAG_TOPICS = {"customs", "delivery", "risks", "fees", "faq"}
# Максимальная длина ответа state_summary в сообщении (Telegram обрежет на 4096).
_STATE_SUMMARY_MAX_LEN = 500
# Ограничение на limit в preview_search (защита от LLM с limit=1000).
_PREVIEW_LIMIT_MIN = 1
_PREVIEW_LIMIT_MAX = 10
_PREVIEW_LIMIT_DEFAULT = 3
# Лимиты на списки в DialogState: unbounded рост = риск OOM/замедления.
_MAX_ASKED_FIELDS = 20
_MAX_ANSWERED_QUESTIONS = 20


def _last_user_question(state: Any) -> str:
    """Последнее сообщение пользователя — вопрос для RAG, если LLM не передал свой.

    Args:
        state: `DialogState`.

    Returns:
        Текст последнего сообщения пользователя или `""`.
    """
    for msg in reversed(state.messages):
        if msg.role == "user":
            return msg.content
    return ""


def _safe_int(value: Any, default: int | None = None) -> int | None:
    """Приводит значение к int без исключения.

    Args:
        value: Любое значение.
        default: Значение по умолчанию.

    Returns:
        `int` или `default`, если значение не приводится.
    """
    if value is None or value == "":
        return default
    try:
        return int(float(value))
    except (TypeError, ValueError, OverflowError):
        return default


async def _ask_rag(
    topic: str, question: str, state: Any, rag: RAGClient | None
) -> str | None:
    """Запрос в RAG-сервис (план внедрения RAG, п. 8.1).

    Args:
        topic: Тема (`customs`, `delivery`, …). Валидируется по `_RAG_TOPICS`.
        question: Вопрос пользователя.
        state: `DialogState` (для `user_id`).
        rag: RAG-клиент или `None`.

    Returns:
        Текст ответа или `None` (RAG выключен/недоступен/низкий confidence →
        fallback на LLM).
    """
    if rag is None or not rag.enabled or not question:
        return None
    normalized_topic = topic.strip().lower() if topic else ""
    context = {
        "tenant": "autoselect",
        "user_id": state.user_id,
        "filters": {"topic": normalized_topic} if normalized_topic in _RAG_TOPICS else {},
    }
    from app.config import get_settings

    threshold = get_settings().rag_confidence_threshold
    try:
        answer = await rag.ask(question, context)
    except RAGUnavailable as exc:
        logger.warning("rag_unavailable: reason=%s user_id=%s", exc, state.user_id)
        return None
    if answer.confidence is None:
        logger.warning("rag_missing_confidence user_id=%s", state.user_id)
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
    """Контекст выполнения tools: БД, Redis, finalize, RAG, квота."""

    session: AsyncSession
    redis: Redis
    finalize: Callable[[SearchCriteria], Awaitable[list[str]]] | None = None
    rag: RAGClient | None = None
    # Квота AI-поиска (free: 3/день, pro: 200, business: None = ∞).
    # Списывается ЗА ВЫДАЧУ РЕЗУЛЬТАТА (preview с вариантами / finalize),
    # не за сообщения — иначе демо-юзер не может пообщаться (п. 8.7).
    quota_limit: int | None = None


@dataclass
class AgentReply:
    """Ответ агента на один ход диалога."""

    session_id: str
    text: str
    options: list[str] = field(default_factory=list)
    status: str = "collecting"
    finalize_ready: bool = False
    turn_count: int = 0
    criteria: dict[str, Any] = field(default_factory=dict)


def _append_bounded(lst: list, value: Any, limit: int) -> None:
    """Добавляет значение в список без дублей и с ограничением длины.

    Args:
        lst: Целевой список.
        value: Значение для добавления.
        limit: Максимальная длина (старые значения вытесняются).
    """
    if value in lst:
        return
    lst.append(value)
    if len(lst) > limit:
        del lst[: len(lst) - limit]


async def _quota_check_and_consume(
    redis: Redis, user_id: int, limit: int | None
) -> bool:
    """Проверяет квоту; при успехе — атомарно списывает.

    Атомарность важна: раздельные `allow` + `consume` дают race condition,
    при котором два параллельных запроса оба проходят проверку и оба
    списывают квоту (TOCTOU). Здесь — `quota_allow` **и** `quota_consume`
    вызываются до поиска, а при откате (`quota_release`) — при ошибке.

    Args:
        redis: Клиент Redis.
        user_id: ID пользователя.
        limit: Лимит или `None` (безлимит).

    Returns:
        `True`, если квота списана (или лимит не задан); `False`, если
        лимит исчерпан.
    """
    if limit is None:
        return True
    if not await quota_allow(redis, user_id, limit):
        return False
    await quota_consume(redis, user_id)
    return True


async def _execute_tool(
    tc: ToolCall, state: DialogState, ctx: ToolContext
) -> ToolOutcome:
    """Валидация результата и обновление DialogState (п. 7.2).

    Args:
        tc: Вызов инструмента от LLM.
        state: Текущее состояние диалога.
        ctx: Контекст (БД, Redis, finalize, RAG, квота).

    Returns:
        `ToolOutcome` с текстом/опциями для ответа.
    """
    args = tc.arguments or {}

    if tc.name == "update_criteria":
        applied = apply_patch(state, args)
        if applied:
            logger.info("tool_update_criteria: fields=%s", applied)
            summary = state_summary(state)[:_STATE_SUMMARY_MAX_LEN]
            return ToolOutcome(text=f"Записал: {summary}.")
        return ToolOutcome(text="Не понял параметры — уточните, пожалуйста.")

    if tc.name == "ask_clarification":
        question = str(args.get("question", "Уточните, пожалуйста:"))
        field_name = str(args.get("field", ""))
        if field_name:
            _append_bounded(state.asked_fields, field_name, _MAX_ASKED_FIELDS)
        raw_options = args.get("options", [])
        options = (
            [str(o) for o in raw_options] if isinstance(raw_options, list) else []
        )
        # Справочные поля: кнопки только из FIELD_OPTIONS — LLM не выдумывает
        # значения (MCP-подход: инструментарий, а не фантазии).
        if field_name in FIELD_OPTIONS:
            if options and options != FIELD_OPTIONS[field_name]:
                logger.info(
                    "clarification_options_replaced: field=%s llm=%s",
                    field_name,
                    options,
                )
            options = FIELD_OPTIONS[field_name]
        return ToolOutcome(text=question, options=options, asked_field=field_name)

    if tc.name == "answer_question":
        topic = str(args.get("topic", "")).strip().lower()
        question = str(args.get("question", "")) or _last_user_question(state)
        llm_answer = str(args.get("answer", "")).strip() or None

        # 1. RAG: фактические вопросы — из базы знаний (план внедрения RAG, фаза 4)
        rag_answer_text = await _ask_rag(topic, question, state, ctx.rag)
        if rag_answer_text:
            _append_bounded(
                state.answered_questions, topic, _MAX_ANSWERED_QUESTIONS
            )
            return ToolOutcome(text=rag_answer_text)

        # 2. Fallback: ответ LLM без контекста (план п. 8.1, шаг 3)
        if llm_answer:
            _append_bounded(
                state.answered_questions, topic, _MAX_ANSWERED_QUESTIONS
            )
            return ToolOutcome(text=llm_answer)
        return ToolOutcome(
            text="Точного ответа у меня сейчас нет — не хочу вводить неверными "
            "цифрами. Рекомендую уточнить у менеджера, он подскажет точно."
        )

    if tc.name == "lookup_reference":
        query = str(args.get("query", ""))[:200]
        found = lookup_reference(query)
        text = found or "В справочнике ничего не нашёл. Назовите марку и модель точнее."
        return ToolOutcome(text=text)

    if tc.name == "preview_search":
        patch = args.get("criteria")
        if isinstance(patch, dict):
            apply_patch(state, patch)
        criteria = state.criteria
        limit_raw = _safe_int(args.get("limit"), _PREVIEW_LIMIT_DEFAULT)
        limit = max(_PREVIEW_LIMIT_MIN, min(_PREVIEW_LIMIT_MAX, limit_raw or _PREVIEW_LIMIT_DEFAULT))

        # Квота ДО поиска: иначе лимит проверяется после затрат, и результат
        # поиска теряется (пользователь видит «лимит исчерпан» без вариантов).
        if not await _quota_check_and_consume(ctx.redis, state.user_id, ctx.quota_limit):
            return ToolOutcome(
                text=(
                    "Лимит AI-поисков на сегодня исчерпан. Сообщения остаются "
                    "бесплатными — могу уточнять критерии. Безлимит и моментальные "
                    "уведомления — в Pro (кнопка «Подписка»)."
                )
            )

        # «Мягкий» поиск: пусто по точным критериям → ослабляем второстепенное
        # (цвет → пробег → спеки → годы → цена) и показываем близкие варианты,
        # а не отписываемся заготовкой (фидбек: «не должен быть завязан»).
        try:
            items, relaxed = await search_with_relaxation(
                ctx.session, criteria, limit=limit
            )
        except Exception:
            # Откат квоты: поиск упал, результат не выдан — квота не должна
            # списываться. Атомарный `_quota_check_and_consume` уже списал;
            # компенсируем `quota_consume` инкрементом в минус (или отдельным
            # `quota_release`, если он есть в сервисе).
            if ctx.quota_limit is not None:
                try:
                    await quota_consume(ctx.redis, state.user_id, delta=-1)
                except Exception:  # noqa: BLE001
                    logger.exception("quota_release_failed user_id=%s", state.user_id)
            raise

        if not items:
            return ToolOutcome(
                text=(
                    "Пока ничего не нашлось по этим критериям. Могу ослабить часть "
                    "параметров или запустить полный поиск по источникам — новые "
                    "варианты пришлю уведомлением. Что ослабим?"
                )
            )
        bullets = "\n".join(
            f"• {i.make or '?'} {i.model or ''} {i.year or ''}, "
            f"{i.price or '?'} {i.currency or ''}, {i.mileage_km or '?'} км"
            for i in items
        )
        if relaxed:
            note = (
                f"Точных совпадений нет, ослабил: {', '.join(relaxed)}. "
                f"Вот близкие варианты:\n{bullets}"
            )
        else:
            note = f"Вот что есть сейчас:\n{bullets}"
        await track(
            ctx.session, state.user_id, EVENT_AI_SEARCH,
            {"kind": "preview", "relaxed": bool(relaxed), "count": len(items)},
        )
        return ToolOutcome(text=note)

    if tc.name == "calc_delivery":
        from app.domain.calc.delivery import calc_delivery, format_breakdown

        price = _safe_int(args.get("price_usd"), 0) or 0
        year = _safe_int(args.get("year"), 0) or 0
        cc = _safe_int(args.get("engine_cc"), 2000) or 2000
        hp = _safe_int(args.get("power_hp"), None)
        if price <= 0 or year < 1980:
            return ToolOutcome(text="Нужны цена в $ и год выпуска — уточните, пожалуйста.")
        try:
            breakdown = calc_delivery(price, cc, year, power_hp=hp)
        except Exception:
            logger.exception("calc_delivery_failed price=%s year=%s cc=%s", price, year, cc)
            return ToolOutcome(text="Не смог рассчитать доставку — проверьте параметры.")
        return ToolOutcome(text=format_breakdown(breakdown))

    if tc.name == "finalize_search":
        # Сначала применяем патч: модель может прислать недостающую марку
        # именно в finalize_search. Проверять полноту до патча — значит
        # отклонить валидный запрос.
        patch = args.get("criteria")
        if isinstance(patch, dict):
            apply_patch(state, patch)
        if not has_critical_fields(state.criteria):
            return ToolOutcome(
                text="Не хватает главных параметров (марка, бюджет и т.п.). "
                "Уточните, пожалуйста."
            )
        # Квота ДО finalize: при ошибке finalize не списываем.
        if not await _quota_check_and_consume(ctx.redis, state.user_id, ctx.quota_limit):
            return ToolOutcome(
                text=(
                    "Лимит AI-поисков на сегодня исчерпан — полный поиск не запущен. "
                    "Безлимит — в Pro (кнопка «Подписка»)."
                )
            )
        try:
            if ctx.finalize:
                await ctx.finalize(state.criteria)
        except Exception:
            # Откат квоты: поиск упал.
            if ctx.quota_limit is not None:
                try:
                    await quota_consume(ctx.redis, state.user_id, delta=-1)
                except Exception:  # noqa: BLE001
                    logger.exception("quota_release_failed user_id=%s", state.user_id)
            raise
        # status меняем ТОЛЬКО после успешного finalize: иначе при ошибке
        # состояние «searching» зависает навсегда.
        state.status = "searching"
        await track(ctx.session, state.user_id, EVENT_AI_SEARCH, {"kind": "finalize"})
        logger.info("tool_finalize_search: user_id=%s", state.user_id)
        return ToolOutcome(
            text="Запускаю поиск по источникам. Пришлю уведомления о новых вариантах.",
            finalized=True,
        )

    logger.warning("unknown_tool: %s", tc.name)
    return ToolOutcome(text="Не понял, что сделать. Переформулируйте, пожалуйста.")


def _to_reply(state: DialogState, text: str, options: list[str]) -> AgentReply:
    """Собирает `AgentReply` из текущего состояния.

    Args:
        state: `DialogState`.
        text: Текст ответа.
        options: Кнопки.

    Returns:
        `AgentReply`.
    """
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


def _is_confirmation(text: str) -> bool:
    """Короткое согласие («всё», «давай», «ок») — не новая инфа.

    Матчит **только точное совпадение** (или фразу, целиком состоящую
    из подтверждений). Иначе «давай подешевле» считалось бы согласием
    и запускало поиск по старым критериям, теряя «подешевле».

    Args:
        text: Сообщение пользователя.

    Returns:
        `True`, если это чистое подтверждение.
    """
    low = text.strip().lower().rstrip("!.?")
    if not low or len(low) > 24:
        return False
    confirmations = {
        "всё", "все", "да", "ага", "угу", "ок", "окей", "хорошо", "ладно",
        "давай", "поехали", "погнали", "запускай", "искать", "ищи", "ну да",
        "верно", "точно", "именно", "всё так", "всё верно", "давай искать",
    }
    if low in confirmations:
        return True
    # Фраза из подтверждений: «да, давай», «ок, искать».
    parts = [p.strip() for p in low.replace(",", " ").split() if p.strip()]
    return bool(parts) and all(p in confirmations for p in parts)


_TURN_LOCK_TTL_SEC = 20  # страхование: лок сгорает, если процесс умер


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
    quota_limit: int | None = None,
) -> AgentReply:
    """Один ход диалога с локом на пользователя (TECHDEBT 4.10).

    Двойной тап по кнопке = два параллельных `run_agent` → оба читают
    одно состояние, оба пишут — второй затирает изменения первого
    (lost update). Redis `SET NX` сериализует ходы: пока обрабатывается
    первый запрос, второй получает `DialogError("busy")` → вызывающий
    показывает кнопки-fallback.

    Raises:
        DialogError: При ошибке LLM или если ход этого пользователя уже
            выполняется.
    """
    lock_key = f"dlg:turn-lock:{user_id}"
    acquired = await redis.set(lock_key, "1", nx=True, ex=_TURN_LOCK_TTL_SEC)
    if not acquired:
        raise DialogError("busy")
    try:
        return await _run_agent_locked(
            user_id,
            text,
            session=session,
            redis=redis,
            llm=llm,
            finalize=finalize,
            store=store,
            rag=rag,
            quota_limit=quota_limit,
        )
    finally:
        await redis.delete(lock_key)


async def _run_agent_locked(
    user_id: int,
    text: str,
    *,
    session: AsyncSession,
    redis: Redis,
    llm: BaseLLMClient,
    finalize: Callable[[SearchCriteria], Awaitable[list[str]]] | None = None,
    store: DialogStore | None = None,
    rag: RAGClient | None = None,
    quota_limit: int | None = None,
) -> AgentReply:
    """Тело хода диалога — вызывать только под локом (`run_agent`)."""
    from app.config import get_settings

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

    # 3. ранний выход: все критичные поля есть И пользователь просто соглашается —
    #    не тратим LLM (п. 7.7.6). Любое содержательное сообщение («плохо ищешь»,
    #    «а есть подешевле?») идёт в LLM: агент должен править критерии и
    #    перевыполнять поиск, а не отвечать заготовкой (фидбек юзера).
    if (
        has_critical_fields(state.criteria)
        and state.status == "collecting"
        and _is_confirmation(text)
    ):
        state.status = "ready"
        await store.save(state)
        return _to_reply(
            state,
            f"Всё главное собрал: {state_summary(state)[:_STATE_SUMMARY_MAX_LEN]}. Запускаем поиск?",
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
        # TECHDEBT 5.1: общий на процесс клиент — состояние circuit
        # breaker живёт сквозь ходы (клиент per-turn делал breaker мёртвым).
        from app.infrastructure.ai.rag_client import get_shared_rag_client

        rag = get_shared_rag_client(redis)
    ctx = ToolContext(
        session=session, redis=redis, finalize=finalize, rag=rag, quota_limit=quota_limit
    )
    reply_text: str | None = result.text
    options: list[str] = []
    for tc in (result.tool_calls or []):
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