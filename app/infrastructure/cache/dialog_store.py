"""`DialogStorePort` поверх Redis: состояние диалога с TTL.

Ключ `dialog:{user_id}` живёт `settings.dialog_ttl_sec` (24 часа): после
простоя диалог поднимается из архива (`dialog_sessions`), а не из Redis.
Повреждённый JSON не роняет запрос — состояние считается отсутствующим.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from app.config import get_settings
from app.domain.dialog.state import DialogState

logger = logging.getLogger(__name__)

# TECHDEBT 4.9: потолок истории сообщений в DialogState (Redis-значение
# не должно расти неограниченно). Всё, что старше, тихо отбрасывается.
MAX_STATE_MESSAGES = 50


def dialog_key(user_id: int) -> str:
    """Ключ состояния диалога пользователя."""
    return f"dialog:{user_id}"


class RedisDialogStore:
    """Хранилище `DialogState` в Redis (`GET`/`SETEX`/`DEL`)."""

    def __init__(self, client: Any, ttl_sec: int | None = None) -> None:
        self._client = client
        self._ttl = ttl_sec or get_settings().dialog_ttl_sec

    async def load(self, user_id: int) -> DialogState | None:
        """Состояние диалога или `None` (нет ключа / битый JSON)."""
        raw = await self._client.get(dialog_key(user_id))
        if not raw:
            return None
        try:
            return DialogState.model_validate_json(raw)
        except Exception:  # noqa: BLE001 — битое состояние = отсутствующее
            logger.exception("dialog_state_corrupted: user_id=%s", user_id)
            return None

    async def save(self, state: DialogState) -> None:
        """Записать состояние с TTL (обновляет `updated_at`).

        TECHDEBT 4.9: история обрезается до `MAX_STATE_MESSAGES` —
        Redis-значение не должно расти неограниченно.
        """
        state.messages = state.messages[-MAX_STATE_MESSAGES:]
        state.updated_at = datetime.now(UTC)
        await self._client.setex(
            dialog_key(state.user_id), self._ttl, state.model_dump_json()
        )

    async def delete(self, user_id: int) -> None:
        """Удалить состояние."""
        await self._client.delete(dialog_key(user_id))
