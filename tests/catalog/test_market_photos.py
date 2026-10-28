"""Фото лотов рынка (07 §1): валидация файла, формат URL, DI-подмена S3.

Реальный MinIO не трогаем: порт `PhotosStoragePort` подменяется
in-memory фейком через extra-провайдер dishka (как ChatPublisher в
test_chats.py) — тесты проверяют контракт, а не хранилище.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
from dishka import Provider, Scope, provide
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.market.ports import PhotosStoragePort
from app.application.market.photos import MAX_PHOTO_BYTES, sniff_image_ext
from app.infrastructure.security.tokens import create_access_token
from app.config import get_settings
from tests.conftest import FakeRedis, install_test_container

pytestmark = pytest.mark.asyncio

# Минимальные валидные файлы по магическим байтам (07 §1).
JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 64
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
WEBP_BYTES = b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"\x00" * 32
TEXT_BYTES = b"#!/bin/sh\nrm -rf /"  # вредоносный «jpeg» по имени файла


class FakePhotosStorage:
    """In-memory порт: ключ → байты; публичный URL того же формата."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.public_base = "http://localhost:9000/market-photos"

    async def upload(self, user_id: int, content: bytes, ext: str) -> str:
        key = f"photos/{user_id}/{uuid4().hex}.{ext}"
        self.objects[key] = content
        return f"{self.public_base}/{key}"


def _photos_override(fake: FakePhotosStorage) -> Provider:
    """DI-подмена фото-хранилища (APP-скоуп, как у ChatPublisher)."""

    class _Override(Provider):
        @provide(scope=Scope.APP, provides=PhotosStoragePort)
        def photos(self) -> PhotosStoragePort:
            return fake

    return _Override()


async def _photos_client(
    session: AsyncSession, redis: FakeRedis, fake: FakePhotosStorage
) -> AsyncIterator[AsyncClient]:
    """Клиент поверх ASGI с подменённым фото-хранилищем."""
    from app.main_api import app as fastapi_app

    previous = fastapi_app.state.dishka_container
    container = install_test_container(
        fastapi_app,
        session=session,
        redis=redis,
        extra=(_photos_override(fake),),
    )
    try:
        transport = ASGITransport(app=fastapi_app)
        async with AsyncClient(
            transport=transport, base_url="http://test"
        ) as http:
            yield http
    finally:
        fastapi_app.state.dishka_container = previous
        await container.close()


# =========================================================================
# Юнит: сниф магических байтов
# =========================================================================


async def test_sniff_image_ext_unit():
    """JPEG/PNG/WebP распознаются; текст и мусор — нет."""
    assert sniff_image_ext(JPEG_BYTES) == "jpg"
    assert sniff_image_ext(PNG_BYTES) == "png"
    assert sniff_image_ext(WEBP_BYTES) == "webp"
    assert sniff_image_ext(TEXT_BYTES) == ""
    assert sniff_image_ext(b"") == ""
    # RIFF без WEBP-тега (например, WAV) — не картинка.
    assert sniff_image_ext(b"RIFF\x24\x00\x00\x00WAVEfmt ") == ""


# =========================================================================
# API: POST /api/market/photos
# =========================================================================


async def test_upload_valid_images(
    session: AsyncSession,
    fake_redis: FakeRedis,
    auth_headers: dict,
    user,
):
    """JPEG/PNG/WebP → 201 + URL photos/{user_id}/{uuid}.{ext}."""
    fake = FakePhotosStorage()
    async for http in _photos_client(session, fake_redis, fake):
        for name, content, ext in (
            ("car.jpg", JPEG_BYTES, "jpg"),
            ("car.png", PNG_BYTES, "png"),
            ("car.webp", WEBP_BYTES, "webp"),
        ):
            resp = await http.post(
                "/api/market/photos",
                files={"file": (name, content, "image/jpeg")},
                headers=auth_headers,
            )
            assert resp.status_code == 201, resp.text
            url = resp.json()["url"]
            prefix = f"{fake.public_base}/photos/{user.telegram_id}/"
            assert url.startswith(prefix)
            # Ключ = uuid4.hex + снифнутное расширение (не имя файла).
            key = url.removeprefix(f"{fake.public_base}/")
            assert key.endswith(f".{ext}")
            uuid_part = key.rsplit("/", 1)[1].removesuffix(f".{ext}")
            assert len(uuid_part) == 32
            int(uuid_part, 16)  # hex от uuid4
            # Байты лежат в «хранилище» как есть.
            assert fake.objects[key] == content


async def test_upload_rejects_disguised_text(
    session: AsyncSession, fake_redis: FakeRedis, auth_headers: dict
):
    """Текст с content-type image/jpeg → 415: верим магическим байтам."""
    fake = FakePhotosStorage()
    async for http in _photos_client(session, fake_redis, fake):
        resp = await http.post(
            "/api/market/photos",
            files={"file": ("evil.jpg", TEXT_BYTES, "image/jpeg")},
            headers=auth_headers,
        )
        assert resp.status_code == 415
        assert "unsupported_media_type" in resp.json()["detail"]
        assert fake.objects == {}  # ничего не записано


async def test_upload_rejects_too_large(
    session: AsyncSession, fake_redis: FakeRedis, auth_headers: dict
):
    """Файл больше 8 МБ → 413 file_too_large (валидный JPEG-префикс)."""
    fake = FakePhotosStorage()
    async for http in _photos_client(session, fake_redis, fake):
        oversized = JPEG_BYTES + b"\x00" * MAX_PHOTO_BYTES
        resp = await http.post(
            "/api/market/photos",
            files={"file": ("big.jpg", oversized, "image/jpeg")},
            headers=auth_headers,
        )
        assert resp.status_code == 413
        assert "file_too_large" in resp.json()["detail"]
        assert fake.objects == {}


async def test_upload_requires_registration(
    session: AsyncSession, fake_redis: FakeRedis, guest_headers: dict
):
    """Гость — 403: как весь контур размещения лотов (§2)."""
    fake = FakePhotosStorage()
    async for http in _photos_client(session, fake_redis, fake):
        resp = await http.post(
            "/api/market/photos",
            files={"file": ("car.jpg", JPEG_BYTES, "image/jpeg")},
            headers=guest_headers,
        )
        assert resp.status_code == 403
        assert "chats_registration_required" in resp.json()["detail"]


async def test_upload_requires_auth(
    session: AsyncSession, fake_redis: FakeRedis
):
    """Без Bearer — 401 (общий гвард контура)."""
    fake = FakePhotosStorage()
    async for http in _photos_client(session, fake_redis, fake):
        resp = await http.post(
            "/api/market/photos",
            files={"file": ("car.jpg", JPEG_BYTES, "image/jpeg")},
        )
        assert resp.status_code == 401


async def test_upload_storage_down_returns_503(
    session: AsyncSession, fake_redis: FakeRedis, auth_headers: dict
):
    """Хранилище выключено/лежит → 503 photos_unavailable (не 500).

    PhotosStorage кидает RuntimeError («photos_disabled: S3_ENDPOINT_URL
    не задан» — пустые S3-креды); сценарий оборачивает в AppError,
    транспорт мапит в 503 с внятным кодом.
    """

    class BrokenStorage:
        async def upload(self, user_id: int, content: bytes, ext: str) -> str:
            raise RuntimeError("photos_disabled: S3_ENDPOINT_URL не задан")

    fake = BrokenStorage()  # type: ignore[assignment]
    async for http in _photos_client(session, fake_redis, fake):  # type: ignore[arg-type]
        resp = await http.post(
            "/api/market/photos",
            files={"file": ("car.jpg", JPEG_BYTES, "image/jpeg")},
            headers=auth_headers,
        )
        assert resp.status_code == 503, resp.text
        assert "photos_unavailable" in resp.json()["detail"]


def _auth_headers_for(telegram_id: int) -> dict:
    """Bearer-заголовок произвольного id (фото не проверяет профиль)."""
    token = create_access_token(telegram_id, get_settings())
    return {"Authorization": f"Bearer {token}"}


async def test_upload_key_scoped_by_user(
    session: AsyncSession, fake_redis: FakeRedis
):
    """Ключ содержит user_id загрузившего — изолирование по юзерам."""
    from datetime import UTC, datetime

    from app.infrastructure.db.models import User

    session.add(
        User(
            telegram_id=777_001,
            username="photo_uploader",
            is_active=True,
            created_at=datetime.now(UTC),
        )
    )
    await session.flush()
    fake = FakePhotosStorage()
    async for http in _photos_client(session, fake_redis, fake):
        resp = await http.post(
            "/api/market/photos",
            files={"file": ("car.jpg", JPEG_BYTES, "image/jpeg")},
            headers=_auth_headers_for(777_001),
        )
        assert resp.status_code == 201
        url = resp.json()["url"]
        assert "/photos/777001/" in url
