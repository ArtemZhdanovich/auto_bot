"""Фасад объектного хранилища (обратная совместимость).

Адаптер `S3Storage` (реализует `ObjectStoragePort`: put/presign_get/delete)
и сборка из настроек живут в `infrastructure/storage/s3.py`. Модуль оставлен
для прежних импортов: `from app.infrastructure.services.storage import ...`.
"""

from app.infrastructure.storage.s3 import (
    S3Storage,
    build_raw_key,
    storage_from_settings,
)

__all__ = ["S3Storage", "build_raw_key", "storage_from_settings"]
