"""Заливка базы знаний (kb/*.md) в RAG-сервис.

Путь каждого файла: MinIO upload → Document в Postgres (owner=SERVICE_USER_ID,
status=uploaded) → публикация сообщения в ingest_queue (worker проиндексирует).

Запуск (из services/rag, при поднятом стеке):
    uv run python scripts/ingest_kb.py [--force]

--force: обновляет документ при совпадении имени файла (удаляет старый чанк-набор
перезаливом: delete-сообщение по doc_id из БД, затем новый ingest).
"""

import asyncio
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import aio_pika
import jwt
import orjson

import os

KB_DIR = Path(__file__).resolve().parent.parent / 'kb'

RABBITMQ_URL = os.environ.get('RABBITMQ_URL', 'amqp://guest:guest@localhost:5672/')
JWT_SECRET = os.environ.get('JWT_SECRET', '')
JWT_ALGORITHM = os.environ.get('JWT_ALGORITHM', 'HS256')
SERVICE_USER_ID = os.environ.get('SERVICE_USER_ID', 'auto_bot')
POSTGRES_DSN = os.environ.get(
    'POSTGRES_DSN', 'postgresql+asyncpg://postgres:postgres@localhost:5432/rag'
)
MINIO_ENDPOINT = os.environ.get('MINIO_ENDPOINT', 'localhost:9000')
MINIO_ACCESS_KEY = os.environ.get('MINIO_ACCESS_KEY', 'minioadmin')
MINIO_SECRET_KEY = os.environ.get('MINIO_SECRET_KEY', 'minioadmin')
MINIO_BUCKET = os.environ.get('MINIO_BUCKET', 'documents')

from rag_core.domain.model import DocStatus, Document  # noqa: E402
from rag_core.infrastructure.file_storage import MinioStorage, compute_file_hash  # noqa: E402
from rag_core.infrastructure.repositories import PostgresDocumentRepository  # noqa: E402

from minio import Minio  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402


def mint_token(ttl: int = 120) -> str:
    now = int(time.time())
    return jwt.encode(
        {'sub': SERVICE_USER_ID, 'jti': uuid.uuid4().hex, 'iat': now, 'exp': now + ttl},
        JWT_SECRET,
        algorithm=JWT_ALGORITHM,
    )


async def main() -> None:
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument('--force', action='store_true')
    args = ap.parse_args()

    files = sorted(KB_DIR.glob('*.md'))
    if not files:
        print(f'No .md files in {KB_DIR}')
        return

    engine = create_async_engine(POSTGRES_DSN, echo=False)
    session_factory = sessionmaker(engine, expire_on_commit=False)
    repo = PostgresDocumentRepository(session_factory)
    storage = MinioStorage(
        Minio(
            MINIO_ENDPOINT,
            access_key=MINIO_ACCESS_KEY,
            secret_key=MINIO_SECRET_KEY,
            secure=False,
        ),
        MINIO_BUCKET,
    )
    # Постквантовое шифрование at-rest (ML-KEM-768 + X25519 hybrid)
    if os.environ.get('ENCRYPT_DOCUMENTS', '').lower() in ('1', 'true', 'yes'):
        from rag_core.infrastructure.file_storage import EncryptedFileStorage

        storage = EncryptedFileStorage(
            storage,
            os.environ['PQ_ENCRYPTION_PUBLIC_KEY'],
            os.environ.get('PQ_ENCRYPTION_PRIVATE_KEY'),
        )

    connection = await aio_pika.connect_robust(RABBITMQ_URL)
    channel = await connection.channel()
    token = mint_token()

    for path in files:
        existing = {
            d.file_name: d for d in await repo.get_all_active()
        }
        doc_id = uuid.uuid4()
        key = f'kb/{path.name}'
        await storage.upload_file(path, key)
        doc = Document(
            id=doc_id,
            file_name=path.name,
            file_path=key,
            file_hash=compute_file_hash(path),
            owner_id=SERVICE_USER_ID,
            access_group=None,
            uploaded_at=datetime.now(timezone.utc),
            status=DocStatus.PENDING,
        )
        old = existing.get(path.name)
        if old is not None:
            if not args.force and old.file_hash == doc.file_hash:
                print(f'skip (unchanged): {path.name}')
                continue
            # перезалив: помечаем старый удалённым, индексируем новый под новым id
            await repo.mark_deleted(old.id)
            await channel.default_exchange.publish(
                aio_pika.Message(
                    body=orjson.dumps(
                        {'token': token, 'doc_id': str(old.id), 'remove_file': False}
                    ),
                    delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                ),
                routing_key='delete_queue',
            )
        await repo.save(doc)
        await channel.default_exchange.publish(
            aio_pika.Message(
                body=orjson.dumps({'token': token, 'doc_id': str(doc_id)}),
                delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            ),
            routing_key='ingest_queue',
        )
        print(f'queued ingest: {path.name} -> doc_id={doc_id}')

    await connection.close()
    await engine.dispose()


if __name__ == '__main__':
    asyncio.run(main())
