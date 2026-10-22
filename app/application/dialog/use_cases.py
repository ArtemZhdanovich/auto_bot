"""Сценарии диалога: ход агента, архив, восстановление, финализация.

Контроллеры (`/api/dialog/*`, `/api/ai/*`) больше не знают ни про Redis, ни
про SQL: всё общение с внешним миром идёт через порты, объявленные здесь.

- `DialogStorePort` — состояние диалога в Redis (TTL, кэш-miss, порча JSON);
- `DialogRepositoryPort` — архив `dialog_sessions`, фильтры `filters`,
  список включённых источников;
- `DialogAgentPort` — один ход LLM-агента (LLM внутри — `LLMPort`);
- `EnabledSourcesPort` + `FetchQueuePort` — постановка задач парсинга.

Квота: лимит берётся из тарифного правила (`TIERS`), списание остаётся за
агентом (`run_agent` → `quota_allow`/`quota_consume`), поэтому контроллер
не считает ни одного счётчика.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import ValidationError

from app.application.dialog.dto import (
    DialogFinalizeCommand,
    DialogFinalizeResult,
    DialogMessageCommand,
    DialogReply,
    DialogRestoreResult,
    DialogSessionView,
    UserProfile,
)
from app.application.dialog.ports import (
    DialogAgentPort,
    DialogAnalyticsPort,
    DialogRepositoryPort,
    DialogStorePort,
    EnabledSourcesPort,
    FetchQueuePort,
    UserProfilePort,
)
from app.application.errors import AppError, Forbidden, InvalidInput, NotFound
from app.domain.dialog.state import Message, new_state
from app.domain.search.criteria import SearchCriteria
from app.domain.subscription.policies import TIERS

logger = logging.getLogger(__name__)


class AiUnavailable(AppError):
    """LLM недоступна или не сконфигурирована."""

    code = "ai_unavailable"
    default_message = "AI temporarily unavailable"


# ---------------------------------------------------------------------------
# Правила
# ---------------------------------------------------------------------------


def ai_daily_limit(profile: UserProfile) -> int | None:
    """Суточный лимит AI-поисков по тарифу (`None` — безлимит)."""
    config = TIERS.get(profile.tier) or TIERS["free"]
    return config.ai_daily_limit


def _criteria_from_payload(payload: dict[str, Any]) -> SearchCriteria:
    """Валидировать критерии из тела запроса.

    Raises:
        InvalidInput: Критерии не проходят доменную валидацию.
    """
    try:
        return SearchCriteria(**payload)
    except ValidationError as exc:
        logger.info("dialog_criteria_invalid fields=%s", list(payload))
        raise InvalidInput("Invalid criteria") from exc


# ---------------------------------------------------------------------------
# Сценарии
# ---------------------------------------------------------------------------


class TriggerFetch:
    """Поставить задачи парсинга по всем включённым источникам.

    Best-effort per source: падение одного источника не мешает остальным.
    Если не поставлен **ни один** — `RuntimeError` (агент откатит квоту).
    """

    def __init__(self, sources: EnabledSourcesPort, queue: FetchQueuePort) -> None:
        self._sources = sources
        self._queue = queue

    async def execute(self, criteria: SearchCriteria) -> tuple[str, ...]:
        """Вернуть имена источников, куда задача действительно поставлена.

        Raises:
            RuntimeError: Включённых источников нет или все постановки упали.
        """
        names = await self._sources.enabled_source_names()
        if not names:
            logger.warning("dialog_fetch_no_sources")
            raise RuntimeError("no enabled sources")

        payload = criteria.model_dump(mode="json")
        queued: list[str] = []
        failed: list[str] = []
        for name in names:
            try:
                await self._queue.enqueue_source(name, payload)
                queued.append(name)
            except Exception:  # noqa: BLE001 — один источник не должен ронять все
                logger.exception("dialog_enqueue_failed source=%s", name)
                failed.append(name)

        if not queued:
            raise RuntimeError(f"failed to enqueue any source (failed={failed})")
        if failed:
            logger.warning(
                "dialog_fetch_partial queued=%s failed=%s", queued, failed
            )
        return tuple(queued)


class SendDialogMessage:
    """Ход диалога: проверка пользователя → агент → архив/analytics.

    Пост-обработка best-effort: LLM уже потрачена и state сохранён, поэтому
    сбой архива или analytics логируется, но ответ пользователю не ломает.
    """

    def __init__(
        self,
        users: UserProfilePort,
        agent: DialogAgentPort,
        store: DialogStorePort,
        repository: DialogRepositoryPort,
        analytics: DialogAnalyticsPort,
    ) -> None:
        self._users = users
        self._agent = agent
        self._store = store
        self._repository = repository
        self._analytics = analytics

    async def execute(self, command: DialogMessageCommand) -> DialogReply:
        """Ответить на сообщение.

        Raises:
            NotFound: Пользователя нет.
            Forbidden: Пользователь деактивирован.
            AiUnavailable: LLM недоступна.
        """
        profile = await self._users.get(command.user_id)
        if profile is None:
            logger.warning("dialog_user_not_found user_id=%s", command.user_id)
            raise NotFound("User not found")
        if not profile.is_active:
            logger.warning("dialog_user_inactive user_id=%s", command.user_id)
            raise Forbidden("User is deactivated")

        reply = await self._agent.reply(
            command.user_id,
            command.text,
            quota_limit=ai_daily_limit(profile),
        )
        await self._archive(command.user_id)
        try:
            await self._analytics.dialog_message(command.user_id)
        except Exception:  # noqa: BLE001 — analytics не должна ронять ответ
            logger.exception("dialog_track_failed user_id=%s", command.user_id)
        return reply

    async def _archive(self, user_id: int) -> None:
        """Сохранить state в архив; ошибка не пробрасывается."""
        try:
            state = await self._store.load(user_id)
            if state is None:
                logger.warning(
                    "dialog_archive_skipped user_id=%s (state is None)", user_id
                )
                return
            await self._repository.archive(state)
            await self._repository.commit()
        except Exception:  # noqa: BLE001 — пост-обработка не должна ронять ответ
            logger.exception("dialog_archive_failed user_id=%s", user_id)


class ResetDialog:
    """Сброс диалога: архив старой сессии и удаление состояния."""

    def __init__(self, store: DialogStorePort, repository: DialogRepositoryPort) -> None:
        self._store = store
        self._repository = repository

    async def execute(self, user_id: int) -> None:
        """Архивировать (если есть) и удалить состояние."""
        state = await self._store.load(user_id)
        if state is not None:
            state.status = "done"
            await self._repository.archive(state)
        await self._store.delete(user_id)
        await self._repository.commit()


class FinalizeDialog:
    """Финализация: критерии → фильтр → задачи парсинга → архив → сброс."""

    def __init__(
        self,
        store: DialogStorePort,
        repository: DialogRepositoryPort,
        fetch: TriggerFetch,
    ) -> None:
        self._store = store
        self._repository = repository
        self._fetch = fetch

    async def execute(self, command: DialogFinalizeCommand) -> DialogFinalizeResult:
        """Зафиксировать поиск.

        Raises:
            InvalidInput: Критерии не проходят валидацию.
            RuntimeError: Ни один источник не поставлен в очередь.
        """
        state = await self._store.load(command.user_id) or new_state(command.user_id)
        if command.criteria:
            state.criteria = _criteria_from_payload(command.criteria)
        state.status = "searching"

        criteria = state.criteria.model_dump(mode="json")
        await self._repository.add_filter(command.user_id, criteria)
        sources = await self._fetch.execute(state.criteria)
        await self._repository.archive(state)
        await self._repository.commit()
        await self._store.delete(command.user_id)

        logger.info(
            "dialog_finalized user_id=%s sources=%s", command.user_id, sources
        )
        return DialogFinalizeResult(criteria=criteria, sources=sources)


class RestoreDialog:
    """Поднять последний диалог из архива, если активного нет."""

    def __init__(self, store: DialogStorePort, repository: DialogRepositoryPort) -> None:
        self._store = store
        self._repository = repository

    async def execute(self, user_id: int) -> DialogRestoreResult | None:
        """Результат восстановления или `None`, если архива нет."""
        active = await self._store.load(user_id)
        if active is not None:
            return DialogRestoreResult(restored=False, reason="active")
        row = await self._repository.last_session(user_id)
        if row is None or not row.messages:
            return None

        state = new_state(user_id)
        state.criteria = _criteria_from_payload(dict(row.criteria or {}))
        state.messages = [Message(**message) for message in row.messages]
        state.turn_count = row.turn_count or len(state.messages)
        state.status = "collecting"
        await self._store.save(state)
        logger.info("dialog_restored user_id=%s from=%s", user_id, row.session_id)
        return DialogRestoreResult(restored=True, session_id=state.session_id)


class GetDialog:
    """Архивный диалог по id — только владельцу (IDOR → 404)."""

    def __init__(self, repository: DialogRepositoryPort) -> None:
        self._repository = repository

    async def execute(self, user_id: int, session_id: str) -> DialogSessionView:
        """Вернуть сессию пользователя.

        Raises:
            NotFound: Сессии нет или она принадлежит другому пользователю.
        """
        row = await self._repository.owned_session(session_id, user_id)
        if row is None:
            logger.info(
                "dialog_foreign_or_missing user_id=%s session_id=%s",
                user_id,
                session_id,
            )
            raise NotFound("Dialog not found")
        return DialogSessionView(
            session_id=row.session_id,
            status=row.status,
            criteria=dict(row.criteria or {}),
            turn_count=row.turn_count,
            messages=tuple(row.messages),
            created_at=row.created_at,
            updated_at=row.updated_at,
        )
