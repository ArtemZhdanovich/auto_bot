"""PhotosStorage — публичный bucket фотографий лотов рынка (MinIO/S3).

Редизайн часть 7 §1: `POST /api/market/photos` кладёт файл в bucket
`market-photos` и отдаёт публичный URL — фронт грузит `<img>` напрямую,
без presign (fallback на presign не нужен: policy public-read ставится
при создании bucket).

Ключ: `photos/{user_id}/{uuid4}.{ext}` — uuid, а не hash контента:
endpoint идемпотентен по факту загрузки (повтор = новый объект, лимит
10 фото на лот держит фронт). Публичный URL собирается из `S3_PUBLIC_URL`
(адрес MinIO, доступный браузеру; `minio:9000` из docker-сети с хоста
не резолвится).

Отдельный класс от `S3Storage`: у фото другой жизненный цикл (нет
retention/raw-префикса, есть bucket policy), общие креды/endpoint —
из тех же настроек `S3_*`.
"""

import asyncio
import logging
import uuid
from typing import Any

logger = logging.getLogger(__name__)

# Content-type по расширению (ext уже проверен снифом магических байтов).
_CONTENT_TYPES = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "webp": "image/webp",
}


class PhotosStorage:
    """Публичное хранилище фото лотов: ensure_bucket (lazy) + upload."""

    def __init__(
        self,
        *,
        endpoint_url: str = "",
        bucket: str = "market-photos",
        public_url: str = "http://localhost:9000",
        access_key: str = "",
        secret_key: str = "",
        region: str = "us-east-1",
        connect_timeout: float = 5.0,
        read_timeout: float = 30.0,
        max_attempts: int = 3,
    ) -> None:
        self.endpoint_url = endpoint_url.rstrip("/")
        self.bucket = bucket
        self.public_url = public_url.rstrip("/")
        self.access_key = access_key
        self.secret_key = secret_key
        self.region = region
        self.connect_timeout = connect_timeout
        self.read_timeout = read_timeout
        self.max_attempts = max_attempts
        self._ensured = False
        self._ensure_lock = asyncio.Lock()

    @property
    def enabled(self) -> bool:
        return bool(self.endpoint_url)

    def _make_client(self) -> Any:
        # Ленивый импорт (как S3Storage): aioboto3 тяжёлый, модуль должен
        # импортироваться в тестах без сетевых зависимостей.
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

    async def ensure_bucket(self) -> bool:
        """Создать bucket при отсутствии + повесить public-read policy.

        Policy перезаписывается идемпотентно: MinIO не отдаёт «текущую»
        policy дешёвой проверкой, а повторный put_bucket_policy той же
        политики безвреден. Ошибка — warning в лог (raw-подход S3Storage):
        upload всё равно попробует и упадёт с внятной ошибкой.
        """
        if not self.enabled:
            return False
        try:
            async with self._make_client() as s3:
                try:
                    await s3.head_bucket(Bucket=self.bucket)
                except Exception:  # noqa: BLE001 — 404 = bucket нет
                    await s3.create_bucket(Bucket=self.bucket)
                    logger.info("photos_bucket_created: %s", self.bucket)
                await s3.put_bucket_policy(
                    Bucket=self.bucket,
                    Policy=_policy_json(self.bucket),
                )
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("photos_ensure_bucket_failed: %s", exc)
            return False


    # --- Зеркалирование фото внешних лотов (docs/REDESIGN/11 §3-§4) ---

    async def mirror_photo(
        self, source: str, source_id: str, index: int, content: bytes, ext: str
    ) -> str:
        """Сохранить фото внешнего лота с индексом в ключе (порядок).

        Ключ mirror/{source}/{source_id}/{index:03d}.{ext}: лексикографическая
        сортировка ключей = порядок фото на источнике.
        """
        key = f"mirror/{source}/{source_id}/{index:03d}.{ext}"
        await self._ensure_once()
        async with self._make_client() as s3:
            await s3.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=content,
                ContentType={
                    "jpg": "image/jpeg", "jpeg": "image/jpeg",
                    "png": "image/png", "webp": "image/webp",
                }.get(ext, "application/octet-stream"),
            )
        return f"{self.public_url}/{self.bucket}/{key}"

    async def has_mirror(self, source: str, source_id: str) -> bool:
        """Есть ли уже зеркало у лота (head префикса)."""
        prefix = f"mirror/{source}/{source_id}/"
        if not self.enabled:
            return False
        async with self._make_client() as s3:
            resp = await s3.list_objects_v2(
                Bucket=self.bucket, Prefix=prefix, MaxKeys=1
            )
            return int(resp.get("KeyCount") or 0) > 0

    async def delete_lot_mirror(self, source: str, source_id: str) -> int:
        """Удалить зеркало фото лота (лот исчез с источника). Возвращает кол-во."""
        prefix = f"mirror/{source}/{source_id}/"
        removed = 0
        if not self.enabled:
            return 0
        async with self._make_client() as s3:
            paginator = s3.get_paginator("list_objects_v2")
            async for page in paginator.paginate(
                Bucket=self.bucket, Prefix=prefix, PaginationConfig={"PageSize": 100}
            ):
                objects = [
                    {"Key": obj["Key"]} for obj in page.get("Contents", [])
                ]
                if objects:
                    await s3.delete_objects(
                        Bucket=self.bucket, Delete={"Objects": objects}
                    )
                    removed += len(objects)
        return removed

    async def _ensure_once(self) -> None:
        """ensure_bucket при первом использовании (один раз на процесс)."""
        if self._ensured:
            return
        async with self._ensure_lock:
            if not self._ensured:
                await self.ensure_bucket()
                self._ensured = True

    async def upload(self, user_id: int, content: bytes, ext: str) -> str:
        """Положить фото и вернуть публичный URL.

        Raises:
            RuntimeError: Хранилище выключено или S3 отклонил объект.
        """
        await self._ensure_once()
        if not self.enabled:
            raise RuntimeError("photos_disabled: S3_ENDPOINT_URL не задан")
        key = f"photos/{user_id}/{uuid.uuid4()}.{ext}"
        content_type = _CONTENT_TYPES.get(ext, "application/octet-stream")
        try:
            async with self._make_client() as s3:
                await s3.put_object(
                    Bucket=self.bucket,
                    Key=key,
                    Body=content,
                    ContentType=content_type,
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("photos_put_failed: %s: %s", key, exc)
            raise RuntimeError(f"photos_put_failed: {key}") from exc
        return self.public_url_for(key)

    def public_url_for(self, key: str) -> str:
        """Публичный URL объекта: `{S3_PUBLIC_URL}/{bucket}/{key}`."""
        return f"{self.public_url}/{self.bucket}/{key}"


def _policy_json(bucket: str) -> str:
    """Policy public-read как JSON-строка (bucket подставлен в Resource)."""
    import json  # noqa: PLC0415

    policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": "*",
                "Action": ["s3:GetObject"],
                "Resource": [f"arn:aws:s3:::{bucket}/*"],
            }
        ]
    }
    return json.dumps(policy)


def photos_storage_from_settings() -> PhotosStorage:
    """Сборка фото-хранилища из настроек (креды общие с raw-payload)."""
    from app.config import get_settings  # noqa: PLC0415

    s = get_settings()
    return PhotosStorage(
        endpoint_url=s.s3_endpoint_url,
        bucket=s.s3_photos_bucket,
        public_url=s.s3_public_url,
        access_key=s.s3_access_key,
        secret_key=s.s3_secret_key.get_secret_value(),
        region=s.s3_region,
    )
