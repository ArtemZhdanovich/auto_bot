"""Тесты raw_payload → S3 (PLAN.md, фаза 6): загрузка при save_listings,
retention-крон, админ-endpoint с presigned URL.

Fallback обязателен: S3 недоступен → объявления сохраняются, raw_key NULL.
"""

from datetime import datetime, timezone
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import app.infrastructure.workers.fetch as fetch_mod
from app.transport.http.routers import admin as admin_mod
from app.infrastructure.services.fetch import compute_content_hash, save_listings
from app.infrastructure.services.storage import S3Storage, build_raw_key
from tests.conftest import FakeSession
from tests.infrastructure.test_fetch import NEW_ROW, UpsertSession, _item, _row


class StubStorage:
    """Заглушка `ObjectStoragePort` (S3Storage.put): пишет тела в память."""

    def __init__(self, fail: bool = False, enabled: bool = True) -> None:
        self.enabled = enabled
        self.fail = fail
        self.puts: list[tuple[str, dict, dict]] = []

    async def put(self, key, body, content_type, metadata=None):
        if self.fail:
            raise RuntimeError(f"s3_put_failed: {key}")
        self.puts.append((key, json.loads(body), metadata or {}))


# ---------- ключ объекта ----------


def test_build_raw_key_format():
    key = build_raw_key("encar", 7, "a" * 64, datetime(2026, 10, 11, tzinfo=timezone.utc))
    assert key == f"raw/encar/2026/10/7_{'a' * 64}.json"


# ---------- save_listings + _upload_raw ----------


async def test_new_listing_uploaded_and_raw_key_saved():
    session = UpsertSession([NEW_ROW])
    storage = StubStorage()
    item = _item()

    stats = await save_listings(session, None, [item], storage=storage)

    assert stats["new"] == 1
    assert len(storage.puts) == 1
    key, payload, meta = storage.puts[0]
    content_hash = compute_content_hash(item)
    assert key == build_raw_key("encar", 7, content_hash, when=item.parsed_at)
    assert payload["source"] == "encar"
    assert "raw" in payload
    # dedup-ключи лежат во внутренней секции payload (контракт S3-объекта)
    assert payload["_internal"]["dedup_keys"]
    assert meta == {
        "source": "encar",
        "listing_id": 7,  # порт получает сырые значения, str делает S3Storage
        "content_hash": content_hash,
        "fetched_at": item.parsed_at,
    }
    # UPDATE listings SET raw_key
    assert "raw_key" in str(session.executed[-1][0])
    assert session.executed[-1][1] == [{"b_id": 7, "b_key": key}]


async def test_unchanged_listing_not_uploaded():
    session = UpsertSession([_row(_item())])
    storage = StubStorage()

    stats = await save_listings(session, None, [_item()], storage=storage)

    assert stats["unchanged"] == 1
    assert storage.puts == []  # хэш не менялся — объект не перезаписываем
    assert len(session.executed) == 1  # только upsert


async def test_s3_failure_falls_back_to_null_raw_key():
    session = UpsertSession([NEW_ROW])
    storage = StubStorage(fail=True)

    stats = await save_listings(session, None, [_item()], storage=storage)

    assert stats["new"] == 1  # объявление сохранено
    assert session.committed is True
    assert len(session.executed) == 2  # upsert + цена; UPDATE raw_key — нет


async def test_disabled_storage_skips_uploads():
    session = UpsertSession([NEW_ROW])
    storage = StubStorage(enabled=False)

    stats = await save_listings(session, None, [_item()], storage=storage)

    assert stats["new"] == 1
    assert storage.puts == []
    assert len(session.executed) == 2


async def test_no_storage_no_upload_sql():
    session = UpsertSession([NEW_ROW])
    stats = await save_listings(session, None, [_item()])
    assert stats["new"] == 1
    assert len(session.executed) == 2


# ---------- retention-крон ----------


class _StubRetention:
    enabled = True

    def __init__(self, keys: list[str]) -> None:
        self.keys = keys
        self.cutoffs: list[datetime] = []

    async def delete_older_than(self, cutoff: datetime) -> list[str]:
        self.cutoffs.append(cutoff)
        return self.keys


class _SessionFactory:
    """session_factory() → async CM с FakeSession (как в test_send_worker)."""

    def __init__(self, session: FakeSession) -> None:
        self.session = session

    def __call__(self) -> "_SessionFactory":
        return self

    async def __aenter__(self) -> FakeSession:
        return self.session

    async def __aexit__(self, *exc: object) -> bool:
        return False


async def test_raw_retention_job_deletes_and_nulls_raw_key(monkeypatch):
    keys = ["raw/encar/2026/04/7_a.json", "raw/kcar/2026/04/9_b.json"]
    storage = _StubRetention(keys)
    session = FakeSession()
    monkeypatch.setattr(fetch_mod, "session_factory", _SessionFactory(session))

    result = await fetch_mod.raw_retention_job({"storage": storage})

    assert result == {"deleted": 2}
    assert storage.cutoffs[0] < datetime.now(timezone.utc)
    # raw_key удалённых объектов обнулён (иначе presigned URL в никуда)
    assert "raw_key = NULL" in str(session.executed[0]) and "ANY(:keys)" in str(
        session.executed[0]
    )
    assert session.executed_params[0]["keys"] == keys
    assert session.committed is True


async def test_raw_retention_job_nothing_to_delete_skips_db(monkeypatch):
    storage = _StubRetention([])
    session = FakeSession()
    monkeypatch.setattr(fetch_mod, "session_factory", _SessionFactory(session))

    result = await fetch_mod.raw_retention_job({"storage": storage})

    assert result == {"deleted": 0}
    assert session.executed == []


async def test_raw_retention_job_skipped_when_disabled():
    result = await fetch_mod.raw_retention_job({})
    assert result == {"deleted": 0, "skipped": True}


# ---------- ensure_bucket ----------


class _S3Error(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.response = {"Error": {"Code": code}}


class _FakeS3:
    """Async CM-клиент: head_bucket raises, create_bucket фиксирует вызов."""

    def __init__(self, head_error: Exception | None) -> None:
        self.head_error = head_error
        self.created = False
        self.puts: list[dict] = []
        self.deleted: list[str] = []
        self.presign_args: dict | None = None

    async def __aenter__(self) -> "_FakeS3":
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def head_bucket(self, Bucket: str) -> None:
        if self.head_error is not None:
            raise self.head_error

    async def create_bucket(self, Bucket: str) -> None:
        self.created = True

    async def put_object(self, **kwargs) -> None:
        self.puts.append(kwargs)

    async def delete_object(self, **kwargs) -> None:
        self.deleted.append(kwargs["Key"])

    async def generate_presigned_url(self, op: str, Params: dict, ExpiresIn: int) -> str:
        self.presign_args = {"op": op, "params": Params, "expires_in": ExpiresIn}
        return f"http://minio.test/{Params['Key']}?sig=1"


@pytest.mark.parametrize("head_error,expect_create", [(None, False), (_S3Error("404"), True)])
async def test_ensure_bucket_creates_when_missing(head_error, expect_create):
    storage = S3Storage(endpoint_url="http://localhost:9000")
    fake = _FakeS3(head_error)
    monkeypatch_s3(storage, fake)

    assert await storage.ensure_bucket() is True
    assert fake.created is expect_create


async def test_ensure_bucket_access_denied_no_create_no_crash():
    storage = S3Storage(endpoint_url="http://localhost:9000")
    fake = _FakeS3(_S3Error("AccessDenied"))
    monkeypatch_s3(storage, fake)

    # Нет прав на CreateBucket → понятный warning, create_bucket НЕ зовём
    assert await storage.ensure_bucket() is False
    assert fake.created is False


def monkeypatch_s3(storage: S3Storage, fake: _FakeS3) -> None:
    storage._make_client = lambda: fake  # noqa: SLF001


# ---------- ObjectStoragePort: put / presign_get / delete ----------


async def test_put_sends_content_type_and_string_metadata():
    storage = S3Storage(endpoint_url="http://localhost:9000")
    fake = _FakeS3(None)
    monkeypatch_s3(storage, fake)
    when = datetime(2026, 10, 11, tzinfo=timezone.utc)

    await storage.put(
        "raw/encar/2026/10/7_abc.json",
        b'{"a": 1}',
        "application/json",
        metadata={"listing_id": 7, "fetched_at": when},
    )

    assert fake.puts[0] == {
        "Bucket": "raw-payloads",
        "Key": "raw/encar/2026/10/7_abc.json",
        "Body": b'{"a": 1}',
        "ContentType": "application/json",
        "Metadata": {"listing_id": "7", "fetched_at": str(when)},
    }


async def test_put_failure_raises_for_caller_fallback():
    class _Broken(_FakeS3):
        async def put_object(self, **kwargs) -> None:
            raise _S3Error("500")

    storage = S3Storage(endpoint_url="http://localhost:9000")
    monkeypatch_s3(storage, _Broken(None))

    with pytest.raises(RuntimeError, match="s3_put_failed"):
        await storage.put("raw/x.json", b"{}", "application/json")


async def test_presign_get_and_delete_use_s3_api():
    storage = S3Storage(endpoint_url="http://localhost:9000", bucket="audit")
    fake = _FakeS3(None)
    monkeypatch_s3(storage, fake)

    url = await storage.presign_get("raw/encar/2026/10/7_abc.json", ttl_sec=600)
    await storage.delete("raw/encar/2026/10/7_abc.json")

    assert url.endswith("?sig=1")
    assert fake.presign_args == {
        "op": "get_object",
        "params": {"Bucket": "audit", "Key": "raw/encar/2026/10/7_abc.json"},
        "expires_in": 600,
    }
    assert fake.deleted == ["raw/encar/2026/10/7_abc.json"]


async def test_presign_get_failure_raises():
    class _Broken(_FakeS3):
        async def generate_presigned_url(self, op, Params, ExpiresIn):
            raise _S3Error("500")

    storage = S3Storage(endpoint_url="http://localhost:9000")
    monkeypatch_s3(storage, _Broken(None))

    with pytest.raises(RuntimeError, match="s3_presign_failed"):
        await storage.presign_get("raw/x.json", ttl_sec=60)


# ---------- админ-endpoint: presigned URL ----------


async def test_admin_raw_returns_presigned_url():
    """Сценарий presign-ит сырой payload (сборка — через контейнер)."""
    from app.application.admin.use_cases import GetListingRawPayload
    from app.infrastructure.db.repositories.admin_sources import (
        SqlAlchemyAdminSourcesRepository,
    )

    session = FakeSession()
    session.set_result(["raw/encar/2026/10/7_abc.json"])

    class _S:
        async def presign_raw(self, key, ttl):
            assert key == "raw/encar/2026/10/7_abc.json"
            assert ttl > 0
            return f"http://minio.test/{key}?sig=1"

    use_case = GetListingRawPayload(
        SqlAlchemyAdminSourcesRepository(session), _S(), ttl_sec=900
    )
    # токен проверяется на уровне роутера — сценарию он не передаётся
    r = await admin_mod.admin_listing_raw(use_case=use_case, listing_id=7)
    assert r["key"] == "raw/encar/2026/10/7_abc.json"
    assert r["url"].endswith("?sig=1")
    assert r["expires_in"] > 0


async def test_admin_raw_404_without_raw_key():
    """raw_key = NULL → RawPayloadNotFound (роутер маппит в 404)."""
    from app.application.admin.use_cases import GetListingRawPayload
    from app.infrastructure.db.repositories.admin_sources import (
        SqlAlchemyAdminSourcesRepository,
    )

    session = FakeSession()
    session.set_result([None])

    use_case = GetListingRawPayload(
        SqlAlchemyAdminSourcesRepository(session),
        storage=None,
        ttl_sec=900,
    )

    with pytest.raises(Exception, match="raw payload"):
        await use_case.execute(7)