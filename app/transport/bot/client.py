"""HTTP-клиент бота к Backend API. Бот не работает с БД напрямую (ТЗ п. 4.1).

TECHDEBT 7.2/7.6: один `httpx.AsyncClient` на инстанс (пул соединений),
ретраи транзиентных сбоев (сеть/5xx) и простой circuit breaker:
`CB_THRESHOLD` ошибок подряд -> `CB_COOLDOWN_SEC` секунд запросы не идут
(быстрый fail вместо таймаута у каждого пользователя).
"""

import asyncio
import logging
import random
import time

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)


class BackendClient:
    # LLM-ход диалога на reasoning-моделях (glm-5-turbo) идёт 15-60 с —
    # обычный таймаут 10 с рвал соединение раньше ответа -> «AI недоступен».
    DIALOG_TIMEOUT = 120.0

    # Circuit breaker (TECHDEBT 7.2): in-memory, однопроцессный бот.
    CB_THRESHOLD = 5
    CB_COOLDOWN_SEC = 30.0

    def __init__(self, base_url: str | None = None, timeout: float = 10.0):
        settings = get_settings()
        self._base_url = (base_url or settings.app_base_url).rstrip("/")
        self._internal_token = (
            settings.internal_token.get_secret_value()
            or settings.app_secret_key.get_secret_value()
        )
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None
        # состояние breaker
        self._failures = 0
        self._open_until = 0.0

    def _http(self) -> httpx.AsyncClient:
        """Ленивый общий клиент (7.6): один пул на процесс бота."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    async def aclose(self) -> None:
        """Закрыть общий HTTP-клиент (shutdown бота)."""
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
        self._client = None

    def _breaker_open(self) -> bool:
        return time.monotonic() < self._open_until

    def _record_failure(self) -> None:
        self._failures += 1
        if self._failures >= self.CB_THRESHOLD:
            self._open_until = time.monotonic() + self.CB_COOLDOWN_SEC
            self._failures = 0
            logger.warning("backend_circuit_open: cooldown=%ss", self.CB_COOLDOWN_SEC)

    def _record_success(self) -> None:
        self._failures = 0
        self._open_until = 0.0

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict | None = None,
        timeout: float | None = None,
        retries: int = 1,
    ) -> dict | None:
        """Запрос с retry (сеть/5xx) и breaker. None — недоступен/ошибка.

        Долгие вызовы (`timeout` > дефолта, напр. LLM-диалог) не ретраим:
        повтор мог бы задвоить ход диалога.
        """
        if self._breaker_open():
            logger.warning("backend_circuit_skipped: %s", path)
            return None
        attempts = retries + 1
        if timeout is not None and timeout > self._timeout:
            attempts = 1  # долгий вызов — без ретрая
        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                resp = await self._http().request(
                    method,
                    f"{self._base_url}{path}",
                    json=json,
                    headers={"X-Internal-Token": self._internal_token},
                    timeout=timeout or self._timeout,
                )
            except httpx.HTTPError as exc:
                last_error = exc
                if attempt + 1 < attempts:
                    await asyncio.sleep(0.5 * 2**attempt + random.uniform(0, 0.2))
                continue
            if resp.status_code >= 500:
                last_error = httpx.HTTPStatusError(
                    f"backend_{resp.status_code}", request=resp.request, response=resp
                )
                if attempt + 1 < attempts:
                    await asyncio.sleep(0.5 * 2**attempt + random.uniform(0, 0.2))
                continue
            if resp.status_code >= 400:
                # 4xx — клиентская ошибка (403 quota и т.п.): не ретраим,
                # breaker не считаем (misconfig не равно «backend лежит»).
                logger.error("backend_client_error: %s %s", path, resp.status_code)
                return None
            self._record_success()
            return resp.json()
        self._record_failure()
        logger.error("backend_request_failed: %s %s", path, last_error)
        return None

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
        # Падение backend не ломает бота (ТЗ п. 1.3 — отказоустойчивость)
        return await self._request("POST", "/api/users/upsert", json=payload)

    async def _post(
        self, path: str, payload: dict, timeout: float | None = None
    ) -> dict | None:
        """POST с internal-токеном. None — backend недоступен/ошибка."""
        return await self._request("POST", path, json=payload, timeout=timeout)

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
        return await self._request("GET", path)

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
        try:
            resp = await self._http().post(
                f"{self._base_url}/api/bot/listings/{listing_id}/analysis",
                json={"user_id": user_id},
                headers={"X-Internal-Token": self._internal_token},
                timeout=self.DIALOG_TIMEOUT,
            )
        except httpx.HTTPError as exc:
            logger.error("backend_analyze_failed: %s", exc)
            self._record_failure()
            return None
        if resp.status_code == 403:
            return {"quota_exceeded": True}  # квота free — сигнал апселла
        if resp.status_code >= 400:
            logger.error("backend_analyze_failed: %s", resp.status_code)
            self._record_failure()
            return None
        self._record_success()
        return resp.json()

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
