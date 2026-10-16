"""Тесты raw_payload → S3 (PLAN.md, фаза 6): загрузка при save_listings,
retention-крон, админ-endpoint с presigned URL.

Fallback обязателен: S3 недоступен → объявления сохраняются, raw_key NULL.
"""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import app.workers.fetch as fetch_mod
from app.api import admin as admin_mod
from app.core.deps_internal import internal_token
from app.services.fetch import compute_content_hash, save_listings
from app.services.storage import S3Storage, build_raw_key
from tests.conftest import FakeSession
from tests.test_fetch import NEW_ROW, UpsertSession, _item, _row


class StubStorage:
    def __init__(self, fail: bool = False, enabled: bool = True) -> None:
        self.enabled = enabled
        self.fail = fail
        self.puts: list[tuple[str, dict, dict]] = []

    async def put_raw(self, key, payload, metadata):
        self.puts.append((key, payload, metadata))
        return not self.fail


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
    assert "raw" in payload and "dedup_keys" in payload
    assert meta == {
        "source": "encar",
        "listing_id": 7,  # put_raw переводит значения метаданных в str
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

    async def __aenter__(self) -> "_FakeS3":
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def head_bucket(self, Bucket: str) -> None:
        if self.head_error is not None:
            raise self.head_error

    async def create_bucket(self, Bucket: str) -> None:
        self.created = True


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


# ---------- админ-endpoint: presigned URL ----------


async def test_admin_raw_returns_presigned_url(monkeypatch):
    session = FakeSession()
    session.set_result([SimpleNamespace(raw_key="raw/encar/2026/10/7_abc.json")])

    class _S:
        async def presign_raw(self, key: str, ttl: int) -> str:
            assert key == "raw/encar/2026/10/7_abc.json"
            return f"http://minio.test/{key}?sig=1"

    monkeypatch.setattr(admin_mod, "storage_from_settings", lambda: _S())
    r = await admin_mod.admin_listing_raw(7, session, internal_token())
    assert r["key"] == "raw/encar/2026/10/7_abc.json"
    assert r["url"].endswith("?sig=1")
    assert r["expires_in"] > 0


async def test_admin_raw_404_without_raw_key(monkeypatch):
    session = FakeSession()
    session.set_result([SimpleNamespace(raw_key=None)])

    with pytest.raises(HTTPException) as exc:
        await admin_mod.admin_listing_raw(7, session, internal_token())
    assert exc.value.status_code == 404