"""S3Storage — `ObjectStoragePort` поверх MinIO/S3 через aioboto3.

Решение открытого вопроса boto3 vs aioboto3: **aioboto3** — fetch/send
воркеры асинхронные, синхронный boto3 блокировал бы event loop на время
сетевого запроса. MinIO совместим с S3 API (endpoint_url).

Ключ: raw/{source}/{yyyy}/{mm}/{listing_id}_{content_hash}.json —
retention по префиксу + бесплатная дедупликация (смена content_hash →
новый объект; без изменений объект не перезаписывается).

Загрузка не критична → fallback: put/presign/retention ошибки логируются,
fetch не падает, listings.raw_key остаётся NULL (аудит теряется,
объявление сохраняется). Порт `ObjectStoragePort.put` сообщает об ошибке
исключением — решение о fallback принимает сценарий (`IngestListings`).
"""

import json
import logging
from datetime import datetime, timezone
from typing import Any

from app.config import get_settings

logger = logging.getLogger(__name__)

_RAW_PREFIX = "raw/"


def _is_access_denied(exc: Exception) -> bool:
    """ClientError с кодом AccessDenied/403 (нет права на операцию)."""
    resp = getattr(exc, "response", None) or {}
    return (resp.get("Error") or {}).get("Code") in {"AccessDenied", "403", "Forbidden"}


def build_raw_key(
    source: str,
    listing_id: int,
    content_hash: str,
    when: datetime | None = None,
) -> str:
    """S3-ключ сырого payload: raw/{source}/{yyyy}/{mm}/{id}_{hash}.json."""
    when = when or datetime.now(timezone.utc)
    return (
        f"{_RAW_PREFIX}{source}/{when:%Y}/{when:%m}/"
        f"{listing_id}_{content_hash}.json"
    )


class S3Storage:
    """Обёртка над aioboto3: порт (put/presign_get/delete) + retention."""

    def __init__(
        self,
        *,
        endpoint_url: str = "",
        bucket: str = "raw-payloads",
        access_key: str = "",
        secret_key: str = "",
        region: str = "us-east-1",
        connect_timeout: float = 5.0,
        read_timeout: float = 30.0,
        max_attempts: int = 3,
    ) -> None:
        self.endpoint_url = endpoint_url.rstrip("/")
        self.bucket = bucket
        self.access_key = access_key
        self.secret_key = secret_key
        self.region = region
        self.connect_timeout = connect_timeout
        self.read_timeout = read_timeout
        self.max_attempts = max_attempts

    @property
    def enabled(self) -> bool:
        return bool(self.endpoint_url)

    async def ensure_bucket(self) -> bool:
        """Создать bucket при отсутствии (DX; ошибка — только в лог).

        Нет прав на CreateBucket (токен только PutObject/GetObject) —
        отдельный warning «создайте вручную», не маскируем под общую ошибку.
        """
        if not self.enabled:
            return False
        try:
            async with self._make_client() as s3:
                try:
                    await s3.head_bucket(Bucket=self.bucket)
                except Exception as exc:  # noqa: BLE001
                    if _is_access_denied(exc):
                        logger.warning(
                            "s3_bucket_missing_no_create_permission: bucket=%s "
                            "— создайте бакет вручную или дайте право CreateBucket",
                            self.bucket,
                        )
                        return False
                    await s3.create_bucket(Bucket=self.bucket)
                    logger.info("s3_bucket_created: %s", self.bucket)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("s3_ensure_bucket_failed: %s", exc)
            return False

    def _make_client(self) -> Any:
        # Ленивый импорт: aioboto3/botocore тяжёлые, и модуль должен
        # импортироваться даже там, где они не нужны (тесты, API-процесс).
        import aioboto3  # noqa: PLC0415
        from botocore.config import Config  # noqa: PLC0415

        session = aioboto3.Session(
            aws_access_key_id=self.access_key or None,
            aws_secret_access_key=self.secret_key or None,
            region_name=self.region,
        )
        return session.client(
            "s3",
            endpoint_url=self.endpoint_url,
            config=Config(
                connect_timeout=self.connect_timeout,
                read_timeout=self.read_timeout,
                retries={"max_attempts": self.max_attempts},
            ),
        )

    # --- ObjectStoragePort --------------------------------------------------

    async def put(
        self,
        key: str,
        body: bytes,
        content_type: str,
        metadata: dict | None = None,
    ) -> None:
        """Положить объект с метаданными; ошибка — `RuntimeError`."""
        if not self.enabled:
            raise RuntimeError(f"s3_disabled: {key}")
        meta = {k: str(v) for k, v in (metadata or {}).items()}
        try:
            async with self._make_client() as s3:
                await s3.put_object(
                    Bucket=self.bucket,
                    Key=key,
                    Body=body,
                    ContentType=content_type,
                    Metadata=meta,
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("s3_put_failed: %s: %s", key, exc)
            raise RuntimeError(f"s3_put_failed: {key}") from exc

    async def presign_get(self, key: str, ttl_sec: int) -> str:
        """Presigned GET для админки (прямой доступ к MinIO не даём)."""
        url = await self.presign_raw(key, ttl_sec)
        if url is None:
            raise RuntimeError(f"s3_presign_failed: {key}")
        return url

    async def delete(self, key: str) -> None:
        """Удалить объект; ошибка — `RuntimeError`."""
        if not self.enabled:
            raise RuntimeError(f"s3_disabled: {key}")
        try:
            async with self._make_client() as s3:
                await s3.delete_object(Bucket=self.bucket, Key=key)
        except Exception as exc:  # noqa: BLE001
            logger.warning("s3_delete_failed: %s: %s", key, exc)
            raise RuntimeError(f"s3_delete_failed: {key}") from exc

    # --- Совместимость с прежним API (bool/None вместо исключений) ----------

    async def put_raw(
        self, key: str, payload: dict[str, Any], metadata: dict[str, Any]
    ) -> bool:
        """Положить JSON-объект с метаданными; `False` вместо исключения."""
        body = json.dumps(payload, ensure_ascii=False, default=str).encode()
        try:
            await self.put(key, body, "application/json", metadata)
        except RuntimeError:
            return False
        return True

    async def presign_raw(self, key: str, ttl: int) -> str | None:
        """Presigned GET; `None` при ошибке или выключенном хранилище."""
        if not self.enabled:
            return None
        try:
            async with self._make_client() as s3:
                return await s3.generate_presigned_url(
                    "get_object",
                    Params={"Bucket": self.bucket, "Key": key},
                    ExpiresIn=ttl,
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("s3_presign_failed: %s: %s", key, exc)
            return None

    async def delete_older_than(self, cutoff: datetime) -> list[str]:
        """Retention: удалить объекты префикса raw/ старше cutoff.

        list_objects_v2 пагинацией → пакетный delete_objects (до 1000 ключей).
        Возвращает ключи удалённых объектов: вызывающий (raw_retention_job)
        обнуляет listings.raw_key, чтобы админка не генерила presigned URL
        на несуществующие объекты (иначе — битые ссылки через 180 дней).
        """
        if not self.enabled:
            return []
        deleted_keys: list[str] = []
        try:
            async with self._make_client() as s3:
                stale: list[str] = []
                paginator = s3.get_paginator("list_objects_v2")
                async for page in paginator.paginate(
                    Bucket=self.bucket, Prefix=_RAW_PREFIX
                ):
                    for obj in page.get("Contents", []):
                        modified = obj["LastModified"]
                        if modified.tzinfo is None:
                            modified = modified.replace(tzinfo=timezone.utc)
                        if modified < cutoff:
                            stale.append(obj["Key"])
                for start in range(0, len(stale), 1000):
                    batch = stale[start : start + 1000]
                    resp = await s3.delete_objects(
                        Bucket=self.bucket,
                        Delete={
                            "Quiet": True,
                            "Objects": [{"Key": k} for k in batch],
                        },
                    )
                    deleted_keys.extend(d["Key"] for d in resp.get("Deleted", []))
        except Exception as exc:  # noqa: BLE001
            logger.warning("s3_retention_failed: %s", exc)
        if deleted_keys:
            logger.info("s3_retention_deleted: n=%d", len(deleted_keys))
        return deleted_keys


def storage_from_settings() -> S3Storage:
    """Сборка хранилища из настроек (fetch-воркер, админ-эндпоинт)."""
    s = get_settings()
    return S3Storage(
        endpoint_url=s.s3_endpoint_url,
        bucket=s.s3_bucket,
        access_key=s.s3_access_key,
        secret_key=s.s3_secret_key,
        region=s.s3_region,
    )
