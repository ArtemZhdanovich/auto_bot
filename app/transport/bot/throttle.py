"""Антифлуд для бота: скользящее окно на пользователя (MVP, in-memory).

Ограничение: у одного процесса бота (один инстанс — ок для MVP).
При рестарте счётчики обнуляются — не страшно.
"""

import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger(__name__)

FLOOD_TEXT = "🐢 Слишком быстро — давай поспокойнее. Подождите пару секунд."


class FloodGate:
    """Не более max_events событий за window_sec на пользователя."""

    _CLEANUP_INTERVAL_SEC = 60.0  # TECHDEBT 7.5: период чистки устаревших записей

    def __init__(self, max_events: int = 15, window_sec: float = 10.0) -> None:
        self.max_events = max_events
        self.window_sec = window_sec
        self._hits: dict[int, list[float]] = {}
        self._warned: dict[int, float] = {}
        self._last_cleanup = 0.0

    def allow(self, user_id: int, now: float | None = None) -> bool:
        """Пропустить событие? (чистая функция времени — тестируема)."""
        t = time.monotonic() if now is None else now
        self._maybe_cleanup(t)
        hits = [x for x in self._hits.get(user_id, []) if t - x < self.window_sec]
        if len(hits) >= self.max_events:
            self._hits[user_id] = hits
            return False
        hits.append(t)
        self._hits[user_id] = hits
        return True

    def _maybe_cleanup(self, now: float) -> None:
        """Раз в `_CLEANUP_INTERVAL_SEC` выкидывает устаревшие записи (7.5).

        Без чистки `_hits`/`_warned` растут пропорционально числу
        пользователей за всё время жизни процесса.
        """
        if now - self._last_cleanup < self._CLEANUP_INTERVAL_SEC:
            return
        self._last_cleanup = now
        stale_hits = [
            uid
            for uid, hits in self._hits.items()
            if not hits or now - hits[-1] >= self.window_sec
        ]
        for uid in stale_hits:
            self._hits.pop(uid, None)
        stale_warned = [
            uid
            for uid, ts in self._warned.items()
            if now - ts >= self.window_sec
        ]
        for uid in stale_warned:
            self._warned.pop(uid, None)

    def should_warn(self, user_id: int, now: float | None = None) -> bool:
        """Предупредить юзера не чаще раза за окно (иначе спам предупреждениями)."""
        t = time.monotonic() if now is None else now
        last = self._warned.get(user_id)
        if last is None or t - last >= self.window_sec:
            self._warned[user_id] = t
            return True
        return False


class FloodMiddleware:
    """aiogram-миддлварь: дропает события флудящих, раз в окно — вежливо предупреждает."""

    def __init__(self, gate: FloodGate | None = None) -> None:
        self.gate = gate or FloodGate()

    async def __call__(
        self,
        handler: Callable[[Any, dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user") or getattr(event, "from_user", None)
        user_id = getattr(user, "id", None)
        if user_id is None or self.gate.allow(user_id):
            return await handler(event, data)
        logger.info("flood_blocked user=%s", user_id)
        if self.gate.should_warn(user_id):
            answer = getattr(event, "answer", None)
            if callable(answer):
                try:
                    await answer(FLOOD_TEXT)
                except Exception:  # ответ не критичен
                    logger.debug("flood_warn_failed", exc_info=True)
        return None  # событие молча дропнуто
