"""Centrifugo v6: браузерные JWT и серверная публикация (§4).

- `centrifugo_jwt` — HS256 connection-токен (sub + exp), секрет
  CENTRIFUGO_SECRET; pyjwt уже в зависимостях (access-токены приложения).
- `CentrifugoPublisher` — POST {url}/api с `Authorization: apikey ...`
  (v6 server API, method=publish). Fire-and-forget: любые ошибки — в
  logger.warning, REST чатов из-за centrifugo не падает (клиенты едут
  поллингом after_id).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import jwt

logger = logging.getLogger(__name__)

# Таймаут publish-API: пуш не должен держать запрос дольше секунды.
_PUBLISH_TIMEOUT_SEC = 3.0


def centrifugo_jwt(sub: str, secret: str, *, ttl_sec: int) -> str:
    """Подписать connection-JWT centrifugo (HS256, sub + iat + exp)."""
    now = datetime.now(UTC)
    claims: dict[str, Any] = {
        "sub": sub,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl_sec)).timestamp()),
    }
    return jwt.encode(claims, secret, algorithm="HS256")


# Dev-заглушка ключа из дефолтов настроек: centrifugo не настроен.
_DEV_API_KEY = "change-me"


class CentrifugoPublisher:
    """Порты `ChatPublisher` + `WsTokenFactory` на одном клиенте centrifugo."""

    def __init__(self, base_url: str, api_key: str, secret: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._secret = secret

    @property
    def enabled(self) -> bool:
        """Centrifugo настроен (не dev-заглушка ключа) — presence осмыслен.

        Онлайн-статусы (09 §1) опираются на реальный presence-API: без
        настроенного ключа честно отвечаем «все офлайн» (`{online: []}`),
        а не жжём очередь пустых запросов.
        """
        return bool(self._api_key) and self._api_key != _DEV_API_KEY

    def issue(self, sub: str, ttl_sec: int) -> str:
        """Браузерный JWT (порт `WsTokenFactory`)."""
        return centrifugo_jwt(sub, self._secret, ttl_sec=ttl_sec)

    async def publish(self, channel: str, data: dict[str, Any]) -> None:
        """Опубликовать событие в канал; ошибки глотаются с warning.

        Каналы: `user:{telegram_id}` / `partner:{id}` (личные каналы
        участников, 09 §1) — общий namespace centrifugo.
        """
        payload = {
            "method": "publish",
            "params": {"channel": channel, "data": data},
        }
        headers = {"Authorization": f"apikey {self._api_key}"}
        try:
            async with httpx.AsyncClient(timeout=_PUBLISH_TIMEOUT_SEC) as client:
                resp = await client.post(
                    f"{self._base_url}/api", json=payload, headers=headers
                )
            if resp.status_code >= 400:
                logger.warning(
                    "centrifugo_publish_rejected channel=%s status=%s body=%s",
                    channel,
                    resp.status_code,
                    resp.text[:200],
                )
        except httpx.HTTPError:
            # Centrifugo недоступен — чат живёт на REST/поллинге.
            logger.warning("centrifugo_unavailable channel=%s", channel)
        except Exception:  # noqa: BLE001 — push не ломает бизнес-операцию
            logger.warning(
                "centrifugo_publish_failed channel=%s", channel, exc_info=True
            )

    async def presence(self, channel: str) -> set[str]:
        """Кто в канале (HTTP API `presence`, 09 §1); ошибка/пусто — пусто.

        Онлайн-статус юзера = присутствие в его личном канале
        `user:{telegram_id}`. Ответ centrifugo:
        `{"result": {"presence": {client: {...}}}}` — возвращаем
        client-id (достаточно булева факта «кто-то есть»).
        """
        payload = {"method": "presence", "params": {"channel": channel}}
        headers = {"Authorization": f"apikey {self._api_key}"}
        try:
            async with httpx.AsyncClient(timeout=_PUBLISH_TIMEOUT_SEC) as client:
                resp = await client.post(
                    f"{self._base_url}/api", json=payload, headers=headers
                )
            if resp.status_code >= 400:
                logger.warning(
                    "centrifugo_presence_rejected channel=%s status=%s body=%s",
                    channel,
                    resp.status_code,
                    resp.text[:200],
                )
                return set()
            body = resp.json()
        except httpx.HTTPError:
            # Centrifugo недоступен — считаем всех офлайн.
            logger.warning("centrifugo_presence_unavailable channel=%s", channel)
            return set()
        except Exception:  # noqa: BLE001 — статус не ломает endpoint
            logger.warning(
                "centrifugo_presence_failed channel=%s", channel, exc_info=True
            )
            return set()
        result = body.get("result") or {}
        clients = result.get("presence") or {}
        return set(clients)
