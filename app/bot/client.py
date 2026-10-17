"""HTTP-клиент бота к Backend API. Бот не работает с БД напрямую (ТЗ п. 4.1)."""

import logging

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)


class BackendClient:
    # LLM-ход диалога на reasoning-моделях (glm-5-turbo) идёт 15-60 с —
    # обычный таймаут 10 с рвал соединение раньше ответа → «AI недоступен».
    DIALOG_TIMEOUT = 120.0

    def __init__(self, base_url: str | None = None, timeout: float = 10.0):
        settings = get_settings()
        self._base_url = (base_url or settings.app_base_url).rstrip("/")
        self._internal_token = settings.internal_token or settings.app_secret_key
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

    async def _post(
        self, path: str, payload: dict, timeout: float | None = None
    ) -> dict | None:
        """POST с internal-токеном. None — backend недоступен/ошибка."""
        try:
            async with httpx.AsyncClient(timeout=timeout or self._timeout) as client:
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
        """POST /api/dialog/message — ход AI-диалога (долгий LLM-вызов)."""
        return await self._post(
            "/api/dialog/message",
            {"user_id": user_id, "text": text},
            timeout=self.DIALOG_TIMEOUT,
        )

    async def confirm_login_link(
        self,
        *,
        code: str,
        telegram_id: int,
        username: str | None,
        first_name: str | None,
        last_name: str | None,
        language_code: str | None,
    ) -> dict | None:
        """POST /api/auth/telegram/link/confirm — вход на сайт через /start login_."""
        return await self._post(
            "/api/auth/telegram/link/confirm",
            {
                "code": code,
                "telegram_id": telegram_id,
                "username": username,
                "first_name": first_name,
                "last_name": last_name,
                "language_code": language_code,
            },
        )

    async def dialog_reset(self, user_id: int) -> dict | None:
        """POST /api/dialog/reset — сброс диалога."""
        return await self._post("/api/dialog/reset", {"user_id": user_id})

    async def dialog_restore(self, user_id: int) -> dict | None:
        """POST /api/dialog/restore — поднять последний диалог из архива."""
        return await self._post("/api/dialog/restore", {"user_id": user_id})

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

    async def quiet_hours(
        self, user_id: int, value: str | None = None, *, clear: bool = False
    ) -> dict | None:
        """POST /api/notifications/settings — тихие часы для всех фильтров.

        value="23:00-08:00" — установить; clear=True — сбросить.
        """
        payload: dict = {"user_id": user_id, "notify_mode": None}
        if clear:
            payload["quiet_hours_clear"] = True
        elif value:
            payload["quiet_hours"] = value
        return await self._post("/api/notifications/settings", payload)

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

    async def get_listing(self, listing_id: int) -> dict | None:
        """GET /api/listings/{id} — карточка для deep link listing_{id} (ТЗ §5.3)."""
        return await self._get(f"/api/listings/{listing_id}")

    async def get_listing_by_url(self, url: str) -> dict | None:
        """GET /api/listings/by-url — «скинь ссылку, покажу карточку»."""
        from urllib.parse import quote_plus

        return await self._get(f"/api/listings/by-url?url={quote_plus(url)}")

    async def analyze_price(self, listing_id: int, user_id: int) -> dict | None:
        """POST /api/bot/listings/{id}/analysis — AI-разбор цены (LLM, до 120 с).

        Отдельная реализация: 403 (квота free) — не ошибка, а сигнал апселла.
        """
        import httpx

        try:
            async with httpx.AsyncClient(timeout=self.DIALOG_TIMEOUT) as client:
                resp = await client.post(
                    f"{self._base_url}/api/bot/listings/{listing_id}/analysis",
                    json={"user_id": user_id},
                    headers={"X-Internal-Token": self._internal_token},
                )
                if resp.status_code == 403:
                    return {"quota_exceeded": True}
                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPError as exc:
            logger.error("backend_analyze_failed: %s", exc)
            return None

    async def search_listings(
        self, params: dict | None = None, limit: int = 5
    ) -> list[dict] | None:
        """GET /api/listings — топ-объявления по критериям (фото-карточки в боте)."""
        found = await self.search_listings_page(params, limit=limit)
        return found[0] if found else None

    async def search_listings_page(
        self, params: dict | None = None, limit: int = 5
    ) -> tuple[list[dict], int] | None:
        """GET /api/listings — объявления + общий счётчик совпадений.

        total нужен боту: мало результатов → карточки в чат, много →
        лента миниаппа с фильтром (п. «формат выдачи»).
        """
        query = {"limit": limit, "offset": 0}
        query.update(params or {})
        qs = "&".join(f"{k}={v}" for k, v in query.items() if v is not None)
        data = await self._get(f"/api/listings?{qs}")
        if data is None:
            return None
        return (data.get("items") or [], int(data.get("total") or 0))

    async def calc_delivery(
        self,
        price_usd: float,
        engine_cc: int,
        year: int,
        power_hp: int | None = None,
    ) -> dict | None:
        """POST /api/calc/delivery — расчёт «под ключ» Корея → РФ."""
        payload: dict = {"price_usd": price_usd, "engine_cc": engine_cc, "year": year}
        if power_hp:
            payload["power_hp"] = power_hp
        return await self._post("/api/calc/delivery", payload)

    async def compare_delivery(self, items: list[dict]) -> dict | None:
        """POST /api/calc/compare — сравнение вариантов «под ключ»."""
        return await self._post("/api/calc/compare", {"items": items})

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
