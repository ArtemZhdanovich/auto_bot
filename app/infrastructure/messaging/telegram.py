"""`NotifierPort` поверх Telegram Bot API."""

from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org"


class TelegramNotifier:
    """Отправка текста в чат через `sendMessage` (HTML-разметка)."""

    def __init__(
        self,
        bot_token: str,
        *,
        api_base: str = TELEGRAM_API,
        timeout: float = 15.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._bot_token = bot_token
        self._api_base = api_base.rstrip("/")
        self._timeout = timeout
        self._transport = transport

    async def send_text(self, chat_id: int, text: str) -> bool:
        """`True`, если Telegram принял сообщение; ошибки — в лог, `False`."""
        url = f"{self._api_base}/bot{self._bot_token}/sendMessage"
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout, transport=self._transport
            ) as client:
                resp = await client.post(
                    url,
                    json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
                )
            if resp.status_code != 200:
                logger.error(
                    "alert_send_bad_status: %s %s", resp.status_code, resp.text[:200]
                )
                return False
            return True
        except httpx.HTTPError as exc:
            logger.error("alert_send_failed: %s", exc)
            return False
