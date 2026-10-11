"""File storage: MinIO adapter and file hashing utilities."""

import hashlib
import os
import tempfile
from pathlib import Path

from minio import Minio

from rag_core.application.interfaces import FileStorage
from rag_core.infrastructure.pq_crypto import decrypt_document, encrypt_document


class MinioStorage(FileStorage):
    """File storage adapter for MinIO."""

    def __init__(self, client: Minio, bucket_name: str) -> None:
        self._client = client
        self._bucket = bucket_name
        self._ensure_bucket()

    def _ensure_bucket(self) -> None:
        if not self._client.bucket_exists(self._bucket):
            self._client.make_bucket(self._bucket)

    async def upload_file(self, local_path: Path, destination_key: str) -> str:
        """Upload a local file and return its storage key."""
        self._client.fput_object(
            self._bucket, destination_key, str(local_path)
        )
        return destination_key

    async def download_file(
        self, source_key: str, destination_path: Path
    ) -> None:
        """Download a stored object to a local path."""
        self._client.fget_object(
            self._bucket, source_key, str(destination_path)
        )

    async def delete_file(self, source_key: str) -> None:
        """Delete a stored object."""
        self._client.remove_object(self._bucket, source_key)


class EncryptedFileStorage(FileStorage):
    """Post-quantum at-rest encryption wrapper (ML-KEM-768 + X25519 hybrid).

    Objects are stored as PQ containers (MAGIC ``AUPQ1``); AAD is the
    storage key, so objects cannot be swapped between keys. Non-PQ
    (legacy) objects are passed through unchanged on download.
    Deletes are pass-through.
    """

    def __init__(self, inner: FileStorage, public_key_hex: str, private_key_hex: str | None = None) -> None:
        self._inner = inner
        self._public_key = public_key_hex
        self._private_key = private_key_hex

    async def upload_file(self, local_path: Path, destination_key: str) -> str:
        plaintext = local_path.read_bytes()
        blob = encrypt_document(
            self._public_key, plaintext, aad=destination_key.encode()
        )
        fd, tmp_name = tempfile.mkstemp(suffix=".pq")
        try:
            with os.fdopen(fd, "wb") as tmp:
                tmp.write(blob)
            return await self._inner.upload_file(Path(tmp_name), destination_key)
        finally:
            Path(tmp_name).unlink(missing_ok=True)

    async def download_file(
        self, source_key: str, destination_path: Path
    ) -> None:
        if not self._private_key:
            raise RuntimeError(
                "PQ_ENCRYPTION_PRIVATE_KEY is required to read encrypted documents"
            )
        fd, tmp_name = tempfile.mkstemp(suffix=".dl")
        os.close(fd)
        try:
            await self._inner.download_file(source_key, Path(tmp_name))
            blob = Path(tmp_name).read_bytes()
            aad = source_key.encode()
            try:
                plaintext = decrypt_document(self._private_key, blob, aad=aad)
            except Exception:
                if blob[:5] == b"AUPQ1":
                    raise
                plaintext = blob  # не-зашифрованный объект — отдаём как есть
            Path(destination_path).write_bytes(plaintext)
        finally:
            Path(tmp_name).unlink(missing_ok=True)

    async def delete_file(self, source_key: str) -> None:
        await self._inner.delete_file(source_key)


def compute_file_hash(file_path: Path) -> str:
    """Compute SHA-256 hash of a file."""
    sha256 = hashlib.sha256()
    with open(file_path, 'rb') as f:
        for chunk in iter(lambda: f.read(4096), b''):
            sha256.update(chunk)
    return sha256.hexdigest()
