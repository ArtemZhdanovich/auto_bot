"""Тесты compute_content_hash / save_listings (PLAN.md, фазы 1–2)."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.domain.listings.unified import UnifiedListing
from app.infrastructure.services.fetch import compute_content_hash, mark_stale, save_listings
from tests.conftest import FakeRedis


class _EvalRedis(FakeRedis):
    """FakeRedis + `eval()` для Lua-скрипта claim из dedup.py (только claim)."""

    async def eval(self, script: str, numkeys: int, *args):
        keys = [str(k) for k in args[:numkeys]]
        if any(k in self.data for k in keys):
            return 0
        ttl = int(args[numkeys]) if len(args) > numkeys else None
        for key in keys:
            self.data[key] = "1"
            if ttl:
                self.ttls[key] = ttl
        return 1


def _item(**kw) -> UnifiedListing:
    base: dict = {
        "source": "encar",
        "source_id": "1",
        "url": "https://encar.com/a/1",
        "title": "Hyundai Palisade 2022",
        "make": "Hyundai",
        "model": "Palisade",
        "year": 2022,
        "price": Decimal("38500"),
        "currency": "USD",
        "mileage_km": 50000,
        "images": ["https://img.example/1.jpg", "https://img.example/2.jpg"],
    }
    base.update(kw)
    return UnifiedListing(**base)


def _row(
    item: UnifiedListing,
    *,
    old_id: int | None = 1,
    was_active: bool = True,
    old_price: Decimal | None = None,
    old_currency: str | None = None,
    old_hash: str | None = None,
) -> dict:
    return {
        "old_id": old_id,
        "old_price": item.price if old_price is None else old_price,
        "old_currency": item.currency if old_currency is None else old_currency,
        "old_hash": compute_content_hash(item) if old_hash is None else old_hash,
        "was_active": was_active,
        "listing_id": old_id or 7,
    }


NEW_ROW = {
    "old_id": None,
    "old_price": None,
    "old_currency": None,
    "old_hash": None,
    "was_active": None,
    "listing_id": 7,
}


class _Result:
    def __init__(self, row):
        self._row = row

    def mappings(self):
        return self

    def first(self):
        return self._row


class UpsertSession:
    """Стаб сессии: execute() отдаёт заготовленные строки по очереди."""

    def __init__(self, rows):
        self._queue = list(rows)
        self.executed: list[tuple[str, dict | None]] = []
        self.committed = False

    async def execute(self, stmt, params=None):
        self.executed.append((str(stmt), params))
        return _Result(self._queue.pop(0) if self._queue else None)

    async def commit(self):
        self.committed = True


# ---------- compute_content_hash ----------


def test_content_hash_stable_when_only_timestamps_change():
    t1 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    a = _item(parsed_at=t1)
    b = _item(parsed_at=t1 + timedelta(days=1))
    assert compute_content_hash(a) == compute_content_hash(b)


def test_content_hash_changes_on_price_with_same_title():
    assert compute_content_hash(_item()) != compute_content_hash(
        _item(price=Decimal("40000"))
    )


def test_content_hash_changes_on_mileage_with_same_price():
    assert compute_content_hash(_item()) != compute_content_hash(_item(mileage_km=60000))


def test_content_hash_changes_on_currency_and_title():
    assert compute_content_hash(_item()) != compute_content_hash(_item(currency="KRW"))
    assert compute_content_hash(_item()) != compute_content_hash(_item(title="Kia"))


def test_content_hash_ignores_image_order():
    reordered = _item(images=["https://img.example/2.jpg", "https://img.example/1.jpg"])
    assert compute_content_hash(_item()) == compute_content_hash(reordered)


# ---------- save_listings ----------


async def test_new_listing_writes_first_price_point():
    session = UpsertSession([NEW_ROW])
    stats = await save_listings(session, None, [_item()])
    assert stats["new"] == 1 and stats["cross_dup"] == 0
    assert stats["price_changed_ids"] == [] and stats["reactivated_ids"] == []
    assert session.committed is True
    assert len(session.executed) == 2  # upsert + первая точка цены
    assert "listing_price_history" in session.executed[1][0]
    assert session.executed[1][1] == {
        "listing_id": 7,
        "price": Decimal("38500"),
        "currency": "USD",
    }


async def test_unchanged_listing_skips_price_point():
    session = UpsertSession([_row(_item())])
    stats = await save_listings(session, None, [_item()])
    assert stats["unchanged"] == 1 and stats["new"] == 0
    assert len(session.executed) == 1  # только upsert


async def test_price_change_writes_price_point():
    session = UpsertSession(
        [_row(_item(), old_price=Decimal("40000"), old_hash="a" * 64)]
    )
    stats = await save_listings(session, None, [_item()])
    assert stats["price_changed"] == 1 and stats["updated"] == 0
    assert stats["price_changed_ids"] == [1]
    assert len(session.executed) == 2
    assert "listing_price_history" in session.executed[1][0]
    assert session.executed[1][1]["price"] == Decimal("38500")


async def test_non_price_change_without_price_point():
    session = UpsertSession([_row(_item(mileage_km=60000), old_hash="b" * 64)])
    stats = await save_listings(session, None, [_item()])
    assert stats["updated"] == 1 and stats["price_changed"] == 0
    assert len(session.executed) == 1


# ---------- перевод тайтлов (фаза 5): translate_ids ----------


async def test_upsert_stores_title_raw_and_new_listing_queued_for_translation():
    session = UpsertSession([NEW_ROW])
    stats = await save_listings(session, None, [_item()])
    assert stats["translate_ids"] == [7]  # NEW — перевод нужен
    params = session.executed[0][1]
    assert params["title_raw"] == "Hyundai Palisade 2022"  # title теперь в БД
    assert "title_raw" in session.executed[0][0]


async def test_upsert_stores_specs_normalized():
    """Спеки (миграция 0016): кузов/топливо/КПП/привод пишутся в каноне."""
    session = UpsertSession([NEW_ROW])
    await save_listings(
        session,
        None,
        [
            _item(
                body_type="SUV",
                fuel="бензин",
                transmission="автомат",
                drive="4wd",
            )
        ],
    )
    params = session.executed[0][1]
    assert params["body_type"] == "suv"
    assert params["fuel"] == "petrol"
    assert params["transmission"] == "automatic"
    assert params["drive"] == "awd"
    # UPSERT не затирает имеющиеся спеки пустыми значениями
    sql = session.executed[0][0]
    assert "COALESCE(EXCLUDED.body_type, listings.body_type)" in str(sql)


async def test_unchanged_listing_not_queued_for_translation():
    session = UpsertSession([_row(_item())])
    stats = await save_listings(session, None, [_item()])
    assert stats["translate_ids"] == []  # хэш не менялся


async def test_content_change_requeues_translation():
    # mileage изменился → хэш изменился → тайтл мог измениться, переводим снова
    session = UpsertSession([_row(_item(mileage_km=60000), old_hash="b" * 64)])
    stats = await save_listings(session, None, [_item(mileage_km=60000)])
    assert stats["updated"] == 1
    assert stats["translate_ids"] == [1]  # listing_id существующей строки


async def test_reactivation_resets_disappeared_at():
    session = UpsertSession([_row(_item(), was_active=False)])
    stats = await save_listings(session, None, [_item()])
    assert stats["reactivated"] == 1
    assert stats["reactivated_ids"] == [1]
    assert "disappeared_at = NULL" in session.executed[0][0]
    assert len(session.executed) == 1  # цена не менялась — точки нет


async def test_reactivation_with_price_change_writes_point():
    session = UpsertSession(
        [_row(_item(), was_active=False, old_price=Decimal("40000"), old_hash="a" * 64)]
    )
    stats = await save_listings(session, None, [_item()])
    assert stats["reactivated"] == 1
    assert stats["reactivated_ids"] == [1]
    assert len(session.executed) == 2
    assert "listing_price_history" in session.executed[1][0]


async def test_cross_source_dup_checked_before_upsert():
    redis = _EvalRedis()
    first = UpsertSession([NEW_ROW])
    await save_listings(first, redis, [_item()])

    second = UpsertSession([])
    stats = await save_listings(second, redis, [_item()])
    assert stats["cross_dup"] == 1
    assert second.executed == []  # до upsert дело не дошло


# ---------- mark_stale / stale_check_job (фаза 3) ----------


class StaleSession:
    """Стаб: execute() по очереди возвращает заготовленные id-списки."""

    def __init__(self, results: list[list]):
        self._results = list(results)
        self.executed: list[str] = []
        self.committed = False

    async def execute(self, stmt):
        self.executed.append(str(stmt))
        rows = self._results.pop(0)

        class _R:
            def scalars(self):
                return self

            def all(self):
                return rows

        return _R()

    async def commit(self):
        self.committed = True


async def test_mark_stale_deactivates_and_skips_cb_sources():
    session = StaleSession([["encar"], [7, 9]])  # сначала CB-источники, затем id
    ids = await mark_stale(session, max_age_days=3)
    assert ids == [7, 9]
    assert session.committed is True
    assert "source_health" in session.executed[0]  # CB-источники учтены
    assert "NOT IN" in session.executed[1]
    assert "disappeared_at" in session.executed[1] and "is_active" in session.executed[1]


async def test_mark_stale_without_cb_sources_no_not_in():
    session = StaleSession([[], [1]])
    ids = await mark_stale(session, max_age_days=3)
    assert ids == [1]
    assert "NOT IN" not in session.executed[1]


async def test_stale_check_job_enqueues_gone(monkeypatch):
    import app.infrastructure.workers.fetch as fetch_mod

    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

    async def fake_mark_stale(session, *, max_age_days=None):
        return [5, 6]

    jobs: list[tuple] = []

    async def fake_enqueue(task, *args):
        jobs.append((task,) + args)
        return "msg-id"

    monkeypatch.setattr(fetch_mod, "session_factory", lambda: _Session())
    monkeypatch.setattr(fetch_mod, "mark_stale", fake_mark_stale)
    monkeypatch.setattr(fetch_mod, "enqueue_task", fake_enqueue)

    result = await fetch_mod.stale_check_job({})
    assert result == {"gone": 2}
    assert jobs == [("listing_status_scan", "gone", [5, 6])]


# --- Обогащение из detail encar (миграция 0017) ------------------------------


class _FakeHttp:
    """Стаб HttpClient: detail payload encar без реальных запросов."""

    last_url: str = ""

    def __init__(self, **kw) -> None:
        pass

    async def get_json(self, url: str, **kw):
        _FakeHttp.last_url = url
        return {
            "spec": {
                "bodyName": "준중형차",  # → sedan (BODY_MAP)
                "fuelName": "디젤",  # → diesel
                "transmissionName": "오토",  # → automatic
                "displacement": 1995,  # → 2.0 л
                "colorName": "청색",  # → blue
            }
        }


class _FakeKcarHttp:
    """Стаб HttpClient: detail-of-ng payload kcar без реальных запросов."""

    last_url: str = ""

    def __init__(self, **kw) -> None:
        pass

    async def get_json(self, url: str, **kw):
        _FakeKcarHttp.last_url = url
        return {
            "data": {
                "rvo": {
                    "carctgr": "대형차",  # → sedan (BODY_MAP знает корейские категории)
                    "fuelTypecdNm": "가솔린",  # → petrol
                    "trnsmsncdNm": "오토",  # → automatic
                    "engdispmnt": 3342,  # → 3.3 л
                    "extrColorNm": "은회색",  # без мапа — как есть
                    "drvgYnNm": "후륜",  # → rwd (DRIVE_MAP, корейский)
                    "vin": "KMHGB41ABBU110600",
                }
            }
        }


@pytest.mark.asyncio
async def test_enrich_fills_specs_from_detail(monkeypatch):
    """enrich_listing_specs: кузов/топливо/КПП/объём/цвет из detail encar."""
    import app.infrastructure.sources.common.http as http_mod
    from types import SimpleNamespace

    from app.infrastructure.services.fetch import enrich_listing_specs

    monkeypatch.setattr(http_mod, "HttpClient", _FakeHttp)
    listing = SimpleNamespace(
        id=42,
        source="encar",
        source_id="42935199",
        body_type=None,
        fuel=None,
        transmission=None,
        engine_volume=None,
        color=None,
    )
    committed = []

    class _Session:
        async def commit(self):
            committed.append(True)

    changed = await enrich_listing_specs(_Session(), listing)
    assert changed is True
    assert listing.body_type == "sedan"
    assert listing.fuel == "diesel"
    assert listing.transmission == "automatic"
    assert listing.engine_volume == 2.0
    assert listing.color == "blue"
    assert committed == [True]
    assert _FakeHttp.last_url.endswith("/42935199")


@pytest.mark.asyncio
async def test_enrich_kcar_fills_specs_and_vin(monkeypatch):
    """enrich_listing_specs (kcar): объём/привод/цвет/VIN из detail-of-ng."""
    import app.infrastructure.sources.common.http as http_mod
    from types import SimpleNamespace

    from app.infrastructure.services.fetch import enrich_listing_specs

    monkeypatch.setattr(http_mod, "HttpClient", _FakeKcarHttp)
    listing = SimpleNamespace(
        id=43,
        source="kcar",
        source_id="61405190",
        body_type=None,
        fuel=None,
        transmission=None,
        engine_volume=None,
        color=None,
        drive=None,
        vin=None,
    )
    committed = []

    class _Session:
        async def commit(self):
            committed.append(True)

    changed = await enrich_listing_specs(_Session(), listing)
    assert changed is True
    assert listing.engine_volume == 3.3  # 3342 см³ → литры
    assert listing.drive == "rwd"  # 후륜
    assert listing.fuel == "petrol"  # 가솔린
    assert listing.transmission == "automatic"  # 오토
    assert listing.vin == "KMHGB41ABBU110600"
    assert listing.body_type == "sedan"  # 대형차 → sedan (BODY_MAP)
    assert committed == [True]
    assert "car-info-detail-of-ng" in _FakeKcarHttp.last_url
    assert "i_sCarCd=EC61405190" in _FakeKcarHttp.last_url


@pytest.mark.asyncio
async def test_enrich_kcar_without_rvo_is_noop(monkeypatch):
    """kcar: пустой rvo («차량정보가 없습니다») — не ошибка, ничего не пишем."""
    import app.infrastructure.sources.common.http as http_mod
    from types import SimpleNamespace

    from app.infrastructure.services.fetch import enrich_listing_specs

    class _EmptyHttp:
        def __init__(self, **kw) -> None:
            pass

        async def get_json(self, url: str, **kw):
            return {"data": {"rvo": None, "message": "차량정보가 없습니다."}}

    monkeypatch.setattr(http_mod, "HttpClient", _EmptyHttp)
    listing = SimpleNamespace(
        id=44, source="kcar", source_id="1", body_type=None, fuel=None,
        transmission=None, engine_volume=None, color=None, drive=None, vin=None,
    )
    committed = []

    class _Session:
        async def commit(self):
            committed.append(True)

    assert await enrich_listing_specs(_Session(), listing) is False
    assert committed == []


@pytest.mark.asyncio
async def test_enrich_skips_complete_and_unknown_source(monkeypatch):
    """Не-enrich: неизвестный источник и уже заполненный encar-лот — без запросов."""
    import app.infrastructure.sources.common.http as http_mod
    from types import SimpleNamespace

    from app.infrastructure.services.fetch import enrich_listing_specs

    calls: list[str] = []

    class _NoHttp:
        def __init__(self, **kw) -> None:
            pass

        async def get_json(self, url: str, **kw):
            calls.append(url)
            return {}

    monkeypatch.setattr(http_mod, "HttpClient", _NoHttp)

    class _Session:
        async def commit(self):
            pass

    che = SimpleNamespace(source="che168", source_id="1", body_type=None, fuel=None,
                          transmission=None, engine_volume=None, color=None)
    assert await enrich_listing_specs(_Session(), che) is False

    full = SimpleNamespace(source="encar", source_id="2", body_type="sedan",
                           fuel="petrol", transmission="automatic",
                           engine_volume=2.0, color="blue")
    assert await enrich_listing_specs(_Session(), full) is False

    full_kcar = SimpleNamespace(source="kcar", source_id="3", body_type="SUV",
                                fuel="petrol", transmission="automatic",
                                engine_volume=2.0, color="blue",
                                drive="awd", vin="KMHXX")
    assert await enrich_listing_specs(_Session(), full_kcar) is False
    assert calls == []


@pytest.mark.asyncio
async def test_enrich_silences_http_errors(monkeypatch):
    """Недоступный encar не роняет enrichment — просто False."""

    import app.infrastructure.sources.common.http as http_mod
    from types import SimpleNamespace

    from app.infrastructure.services.fetch import enrich_listing_specs

    class _BrokenHttp:
        def __init__(self, **kw) -> None:
            pass

        async def get_json(self, url: str, **kw):
            raise RuntimeError("encar down")

    monkeypatch.setattr(http_mod, "HttpClient", _BrokenHttp)
    listing = SimpleNamespace(source="encar", source_id="3", body_type=None,
                              fuel=None, transmission=None,
                              engine_volume=None, color=None)

    class _Session:
        async def commit(self):
            pass

    assert await enrich_listing_specs(_Session(), listing) is False
    assert listing.body_type is None  # ничего не записали
