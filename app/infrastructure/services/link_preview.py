"""Загрузка внешних страниц для OG-превью с SSRF-фильтром (§5, §10).

Разрешены только http/https; DNS резолвится `socket.getaddrinfo`, и ВСЕ
адреса хоста обязаны быть публичными (приватные/loopback/link-local/
reserved/multicast → LinkBlockedError → 400). Редиректы — не более одного,
каждый хоп проверяется тем же фильтром. Тело — до 512KB (OG-теги в <head>),
таймаут 3с; `follow_redirects=False`, Location разрешается вручную.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
from urllib.parse import urljoin, urlsplit

import httpx

from app.application.chat.ports import LinkBlockedError

logger = logging.getLogger(__name__)

_ALLOWED_SCHEMES = ("http", "https")
_MAX_BODY_BYTES = 512 * 1024
_TIMEOUT_SEC = 3.0
_MAX_REDIRECTS = 1
_REDIRECT_STATUSES = (301, 302, 303, 307, 308)
_USER_AGENT = "auto-bot-preview/1.0 (+link-preview)"


def _assert_public_host(url: str) -> None:
    """Схема http/https и все IP хоста — публичные (иначе LinkBlockedError).

    Проверяем каждый адрес getaddrinfo: DNS-rebinding с одним публичным
    и одним приватным A-рекордом не должен проходить.
    """
    parsed = urlsplit(url)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise LinkBlockedError(f"scheme_forbidden: {parsed.scheme or '?'}")
    host = parsed.hostname
    if not host:
        raise LinkBlockedError("host_missing: некорректный URL")
    try:
        infos = socket.getaddrinfo(
            host,
            parsed.port or (443 if parsed.scheme == "https" else 80),
            proto=socket.IPPROTO_TCP,
        )
    except socket.gaierror as exc:
        raise LinkBlockedError(f"dns_failed: {host}") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            raise LinkBlockedError(f"private_address_forbidden: {host}")


class HttpLinkFetcher:
    """Порт `LinkFetcherPort`: HTML-голова страницы с SSRF-фильтром."""

    async def fetch(self, url: str) -> str | None:
        """HTML (до 512KB) или None (недоступна); SSRF → LinkBlockedError.

        Raises:
            LinkBlockedError: Схема/хост запрещены (клиенту 400).
        """
        current = url
        hops = 0
        async with httpx.AsyncClient(
            follow_redirects=False, timeout=_TIMEOUT_SEC
        ) as client:
            while True:
                # DNS блокирует event loop — уводим в поток (кэш ОС быстр).
                await asyncio.to_thread(_assert_public_host, current)
                try:
                    async with client.stream(
                        "GET", current, headers={"User-Agent": _USER_AGENT}
                    ) as resp:
                        if resp.status_code in _REDIRECT_STATUSES:
                            location = resp.headers.get("Location")
                            if not location or hops >= _MAX_REDIRECTS:
                                return None
                            hops += 1
                            current = urljoin(current, location)
                            continue
                        if resp.status_code != 200:
                            return None
                        buffer = bytearray()
                        async for chunk in resp.aiter_bytes():
                            buffer.extend(chunk)
                            if len(buffer) >= _MAX_BODY_BYTES:
                                break
                        charset = resp.charset_encoding or "utf-8"
                        return bytes(buffer[:_MAX_BODY_BYTES]).decode(
                            charset, errors="replace"
                        )
                except httpx.HTTPError:
                    logger.info("preview_fetch_failed url=%s", current)
                    return None
