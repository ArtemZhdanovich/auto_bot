"""Адаптер источника для ParserDoctor: живая страница и проверка селекторов.

`_parse` вызывается синхронно на уже скачанном HTML — сети здесь нет,
поэтому предложение LLM проверяется до записи конфига.
"""

from __future__ import annotations

import logging
from typing import Any

from app.application.admin.dto import ListPage
from app.infrastructure.sources.che168 import HEADERS

logger = logging.getLogger(__name__)

MIN_ITEMS = 3  # минимум карточек, чтобы принять ремонт
MIN_PRICE_RATE = 0.5  # доля карточек с ценой


class AdapterProbe:
    """`ParserProbe` поверх конкретного адаптера источника."""

    def __init__(self, adapter: Any) -> None:
        self._adapter = adapter

    async def fetch_list_page(self, config: dict[str, Any]) -> ListPage:
        """Живой HTML страницы списка тем же HttpClient (прокси/ретраи).

        Сетевой сбой → `ListPage(error=...)`: `raise_for_status` в HttpClient
        превращает 4xx/5xx в FetchError со строкой "HTTP <code> for <url>",
        код вытаскивает триаж.
        """
        base_url = str(config.get("base_url", "")).rstrip("/")
        list_path = str(config.get("list_path", ""))
        if not base_url:
            return ListPage(error="no base_url in source config")
        try:
            resp = await self._adapter.http.get(base_url + list_path, headers=HEADERS)
        except Exception as exc:  # noqa: BLE001 — триажим любую сетевую ошибку
            logger.info("parser_doctor_fetch_failed: %s", exc)
            return ListPage(error=str(exc))
        return ListPage(status=resp.status_code, html=resp.text)

    def validate_selectors(self, config: dict[str, Any], html: str) -> int | None:
        """Спарсить html адаптером с новыми селекторами; вернёт число карточек.

        Returns:
            Число карточек или `None`, если селекторы структурно невалидны
            (0 карточек / дубли ID / мало цен).
        """
        probe = type(self._adapter)(source_name="doctor-probe", config=config)
        items = probe._parse(html)  # noqa: SLF001 — синхронный парс без сети
        if len(items) < MIN_ITEMS:
            return None
        ids = [item.source_id for item in items]
        if len(set(ids)) != len(ids):
            return None
        with_price = sum(1 for item in items if item.price is not None)
        if with_price / len(items) < MIN_PRICE_RATE:
            return None
        return len(items)
