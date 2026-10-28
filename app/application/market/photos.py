"""Загрузка фото лота рынка: валидация + публичное хранилище (07 §1).

Файл приходит multipart'ом (<=8 МБ), тип проверяется снифом магических
байтов — content-type заголовку верить нельзя (подделка за секунду):
- JPEG: `FF D8 FF`;
- PNG: `89 50 4E 47` (`\x89PNG`);
- WebP: `RIFF` + `WEBP` на смещении 8.

Ошибки — отдельные коды (`file_too_large`, `unsupported_media_type`),
транспорт мапит их в 413/415.
"""

from __future__ import annotations

import logging

from app.application.errors import AppError
from app.application.market.ports import PhotosStoragePort

logger = logging.getLogger(__name__)

# Лимит файла (07 §1): 8 МБ.
MAX_PHOTO_BYTES = 8 * 1024 * 1024

# Магические префиксы: (число байт, смещение WEBP-подписи внутри RIFF).
_JPEG_MAGIC = b"\xff\xd8\xff"
_PNG_MAGIC = b"\x89PNG"
_RIFF_MAGIC = b"RIFF"
_WEBP_TAG = b"WEBP"


class PhotoTooLarge(AppError):
    """Файл больше 8 МБ (транспорт → 413)."""

    code = "file_too_large"
    default_message = "file_too_large: файл больше 8 МБ"


class UnsupportedPhotoType(AppError):
    """Файл не JPEG/PNG/WebP по магическим байтам (транспорт → 415)."""

    code = "unsupported_media_type"
    default_message = (
        "unsupported_media_type: только JPEG, PNG или WebP"
    )


class PhotosUnavailable(AppError):
    """Хранилище выключено или S3 отклонил объект (транспорт → 503).

    PhotosStorage кидает RuntimeError («photos_disabled: S3_ENDPOINT_URL
    не задан» / «photos_put_failed»); без этой обёртки transport отдаёт
    непрозрачный 500. 503 с кодом = внятная ошибка и ретрай клиентом.
    """

    code = "photos_unavailable"
    default_message = (
        "photos_unavailable: хранилище фото недоступно, попробуйте позже"
    )


def sniff_image_ext(content: bytes) -> str:
    """Расширение по магическим байтам; `''` — формат не распознан."""
    if content.startswith(_JPEG_MAGIC):
        return "jpg"
    if content.startswith(_PNG_MAGIC):
        return "png"
    if content.startswith(_RIFF_MAGIC) and content[8:12] == _WEBP_TAG:
        return "webp"
    return ""


class UploadMarketPhoto:
    """POST /api/market/photos — валидация и загрузка (07 §1).

    Валиден файл <=8 МБ с сигнатурой JPEG/PNG/WebP (content-type из
    multipart не проверяем — магические байты надёжнее). Идемпотентность
    по файлу не нужна: каждый upload = новый uuid-ключ, лимит 10 фото на
    лот держит фронт.
    """

    def __init__(self, storage: PhotosStoragePort) -> None:
        self._storage = storage

    async def execute(self, user_id: int, content: bytes) -> str:
        """Проверить файл и положить в bucket; вернуть публичный URL.

        Raises:
            PhotoTooLarge: Файл больше 8 МБ (413).
            UnsupportedPhotoType: Сигнатура не JPEG/PNG/WebP (415).
            PhotosUnavailable: Хранилище выключено/S3 недоступен (503).
        """
        if len(content) > MAX_PHOTO_BYTES:
            raise PhotoTooLarge()
        if not content:
            raise UnsupportedPhotoType()
        ext = sniff_image_ext(content)
        if not ext:
            raise UnsupportedPhotoType()
        try:
            url = await self._storage.upload(user_id, content, ext)
        except RuntimeError as exc:
            raise PhotosUnavailable() from exc
        logger.info(
            "market_photo_uploaded user_id=%s size=%s ext=%s",
            user_id,
            len(content),
            ext,
        )
        return url
