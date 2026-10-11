"""HTTP-клиент бота к Backend API. Бот не работает с БД напрямую (ТЗ п. 4.1)."""

import logging

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)


class BackendClient:
    def __init__(self, base_url: str | None = None, timeout: float = 10.0):
        settings = get_settings()
        self._base_url = (base_url or settings.app_base_url).rstrip("/")
        self._internal_token = settings.app_secret_key
        self._timeout = timeout

    async def upsert_user(
        self,
        *,
        telegram_id: int,
        username: str | None,
        first_name: str | None,
        last_name: str | None,
        language_code: str | None,
    ) -> dict | None:
        """POST /api/users/upsert — создать/обновить пользователя."""
        payload = {
            "telegram_id": telegram_id,
            "username": username,
            "first_name": first_name,
            "last_name": last_name,
            "language_code": language_code,
        }
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(
                    f"{self._base_url}/api/users/upsert",
                    json=payload,
                    headers={"X-Internal-Token": self._internal_token},
                )
                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPError as exc:
            # Падение backend не ломает бота (ТЗ п. 1.3 — отказоустойчивость)
            logger.error("backend_upsert_user_failed: %s", exc)
            return None

    async def _post(self, path: str, payload: dict) -> dict | None:
        """POST с internal-токеном. None — backend недоступен/ошибка."""
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(
                    f"{self._base_url}{path}",
                    json=payload,
                    headers={"X-Internal-Token": self._internal_token},
                )
                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPError as exc:
            logger.error("backend_post_failed: %s %s", path, exc)
            return None

    async def dialog_message(self, user_id: int, text: str) -> dict | None:
        """POST /api/dialog/message — ход AI-диалога."""
        return await self._post(
            "/api/dialog/message", {"user_id": user_id, "text": text}
        )

    async def dialog_reset(self, user_id: int) -> dict | None:
        """POST /api/dialog/reset — сброс диалога."""
        return await self._post("/api/dialog/reset", {"user_id": user_id})

    async def dialog_finalize(
        self, user_id: int, criteria: dict | None = None
    ) -> dict | None:
        """POST /api/dialog/finalize — запуск поиска по критериям."""
        return await self._post(
            "/api/dialog/finalize", {"user_id": user_id, "criteria": criteria}
        )

    async def notification_settings(
        self, user_id: int, notify_mode: str
    ) -> dict | None:
        """POST /api/notifications/settings — /stop //resume (ТЗ п. 8.2, 8.6)."""
        return await self._post(
            "/api/notifications/settings",
            {"user_id": user_id, "notify_mode": notify_mode},
        )

    async def _get(self, path: str) -> dict | None:
        """GET с internal-токеном. None — backend недоступен/ошибка."""
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(
                    f"{self._base_url}{path}",
                    headers={"X-Internal-Token": self._internal_token},
                )
                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPError as exc:
            logger.error("backend_get_failed: %s %s", path, exc)
            return None

    async def subscription_status(self, user_id: int) -> dict | None:
        """GET /api/users/{id}/subscription — тариф для /subscribe (ТЗ п. 8.7)."""
        return await self._get(f"/api/users/{user_id}/subscription")

    async def prepare_payment(
        self, user_id: int, tier: str, period_days: int
    ) -> dict | None:
        """POST /api/payments/prepare — pending-платёж перед sendInvoice."""
        return await self._post(
            "/api/payments/prepare",
            {"user_id": user_id, "tier": tier, "period_days": period_days},
        )

    async def payment_success(
        self,
        *,
        payment_id: int,
        user_id: int,
        provider_payment_id: str,
        amount: int,
    ) -> dict | None:
        """POST /api/payments/success — активация подписки после оплаты."""
        return await self._post(
            "/api/payments/success",
            {
                "payment_id": payment_id,
                "user_id": user_id,
                "provider_payment_id": provider_payment_id,
                "amount": amount,
            },
        )
