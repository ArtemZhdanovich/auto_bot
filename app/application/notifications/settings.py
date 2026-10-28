"""Сценарии уведомлений: настройки фильтров и ручной запуск скана.

Правила, которые раньше жили в роутере, собраны здесь:

- 404 для неизвестного `user_id`, 403 для деактивированного;
- tier-bypass: Free не может включить `instant` (как в
  `filters.py::create_filter`);
- `quiet_hours` и `quiet_hours_clear` взаимоисключающи;
- пустой патч не трогает `updated_at` у N фильтров;
- скан идемпотентен по `SET NX EX 60` и отдаёт 503 при недоступности
  очереди, снимая dedup-ключ.

Очередь и dedup — порты (`Enqueuer`, `IdempotencyGuard`): сценарий не знает
про RabbitMQ и Redis.
"""

from __future__ import annotations

import logging

from app.application.catalog.dto import (
    NotifySettingsCommand,
    NotifySettingsResult,
    ScanResult,
)
from app.application.catalog.ports import Enqueuer, NotifySettingsRepository
from app.application.errors import AppError, Conflict, Forbidden, InvalidInput, NotFound
from app.application.shared.ports import IdempotencyGuard
from app.domain.subscription.policies import tier_config

logger = logging.getLogger(__name__)

SCAN_DEDUP_KEY = "notify:scan:dedup"
SCAN_DEDUP_TTL_SEC = 60
SCAN_JOB = "notify_new_listings"


class QueueUnavailable(AppError):
    """Очередь заданий недоступна: запрос можно повторить позже."""

    code = "queue_unavailable"
    default_message = "Queue unavailable, retry later"


class UpdateNotifySettings:
    """Применить режим/тихие часы ко всем фильтрам пользователя."""

    def __init__(self, settings: NotifySettingsRepository) -> None:
        self._settings = settings

    async def execute(self, command: NotifySettingsCommand) -> NotifySettingsResult:
        """Обновить фильтры.

        Args:
            command: Настройки; `quiet_hours_clear` сбрасывает тихие часы.

        Returns:
            `NotifySettingsResult` с числом обновлённых фильтров.

        Raises:
            NotFound: Пользователя нет.
            Forbidden: Пользователь деактивирован или `instant` без Pro.
            InvalidInput: `quiet_hours` и `quiet_hours_clear` вместе.
        """
        target = await self._settings.target(command.user_id)
        if target is None:
            raise NotFound("User not found")
        if not target.is_active:
            raise Forbidden("User is deactivated")

        # Tier-bypass guard: instant при публикации — только Business;
        # Free/Pro получают дайджест (раз в неделю / раз в день).
        if command.notify_mode == "instant" and not tier_config(target).instant:
            raise Forbidden(
                "instant_business_only: уведомление сразу при публикации — "
                "тариф Business; на Pro подборка приходит дайджестом раз в "
                "день (/subscribe)"
            )

        if command.quiet_hours_clear and command.quiet_hours is not None:
            raise InvalidInput(
                "quiet_hours and quiet_hours_clear are mutually exclusive"
            )

        fields: list[str] = []
        if command.notify_mode is not None:
            fields.append("notify_mode")
        if command.quiet_hours is not None or command.quiet_hours_clear:
            fields.append("quiet_hours")

        if not fields:
            # Ничего менять — не трогаем updated_at у N фильтров.
            return NotifySettingsResult(
                user_id=command.user_id, updated=0, detail="nothing_to_change"
            )

        updated = await self._settings.update_filters(
            command.user_id,
            notify_mode=command.notify_mode,
            quiet_hours=command.quiet_hours,
            clear_quiet_hours=command.quiet_hours_clear,
        )
        logger.info(
            "notification_settings_updated user_id=%s fields=%s updated=%d",
            command.user_id, fields, updated,
        )
        return NotifySettingsResult(user_id=command.user_id, updated=updated)


class TriggerScan:
    """Ручной запуск скана фильтров (обычно его делает Fetch Worker)."""

    def __init__(
        self,
        guard: IdempotencyGuard,
        enqueuer: Enqueuer,
        *,
        key: str = SCAN_DEDUP_KEY,
        ttl_sec: int = SCAN_DEDUP_TTL_SEC,
        job: str = SCAN_JOB,
    ) -> None:
        self._guard = guard
        self._enqueuer = enqueuer
        self._key = key
        self._ttl_sec = ttl_sec
        self._job = job

    async def execute(self) -> ScanResult:
        """Поставить задание скана.

        Raises:
            Conflict: Скан уже запускался в пределах `ttl_sec`.
            QueueUnavailable: Очередь недоступна (dedup-ключ снят).
        """
        if not await self._guard.acquire(self._key, self._ttl_sec):
            raise Conflict("Notification scan already running, retry in a minute")

        try:
            job_id = await self._enqueuer.enqueue(self._job)
        except Exception as exc:  # noqa: BLE001 — любая ошибка брокера = 503
            logger.exception("notification_scan_enqueue_failed")
            await self._release()
            raise QueueUnavailable("Queue unavailable, retry later") from exc

        logger.info("notification_scan_triggered job_id=%s", job_id)
        return ScanResult(job_id=job_id)

    async def _release(self) -> None:
        """Снять dedup-ключ, чтобы повтор не ждал минуту."""
        try:
            await self._guard.release(self._key)
        except Exception:  # noqa: BLE001
            logger.warning("notification_scan_dedup_cleanup_failed", exc_info=True)
