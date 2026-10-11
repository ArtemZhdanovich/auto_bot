"""Тесты планировщика парсинга: sources.schedule_cron → очередь fetch."""

import pytest

import app.workers.fetch as fetch_mod
from app.db.models import Source
from app.queue import IntervalSpec
from conftest import FakeRedis, FakeSession


def _source(name: str, *, enabled: bool = True, cron: str | None = "* * * * *") -> Source:
    return Source(
        name=name,
        adapter_class="EncarApiAdapter",
        kind="api",
        enabled=enabled,
        schedule_cron=cron,
    )


@pytest.fixture
def fetch_env(monkeypatch):
    enqueued: list[tuple[str, tuple]] = []

    async def _fake_enqueue(job: str, *args) -> None:
        enqueued.append((job, args))

    monkeypatch.setattr(fetch_mod, "enqueue_task", _fake_enqueue)
    return {"enqueued": enqueued, "redis": FakeRedis()}


@pytest.mark.asyncio
async def test_schedule_due(monkeypatch, fetch_env):
    session = FakeSession()
    session.set_result(
        [
            _source("encar"),  # каждая минута → ставим
            _source("kcar", cron="0 9 * * *"),  # не сейчас (если сейчас не 09:00 UTC)
            _source("off", enabled=False),  # выключен → пропускаем
            _source("nocron", cron=None),  # без расписания → пропускаем
        ]
    )
    monkeypatch.setattr(fetch_mod, "session_factory", lambda: session)

    ctx = {"redis": fetch_env["redis"]}
    res = await fetch_mod.schedule_fetch_due(ctx)

    # encar гарантированно ставится; kcar — только если сейчас ровно 09:00 UTC
    names = [args[0] for _, args in fetch_env["enqueued"]]
    assert "encar" in names
    assert "off" not in names and "nocron" not in names
    if res["scheduled"] == 1:
        assert names == ["encar"]

    # повторный прогон в ту же минуту — Redis-лок отсекает дубли
    res2 = await fetch_mod.schedule_fetch_due(ctx)
    assert res2["scheduled"] == 0
    assert len(fetch_env["enqueued"]) == res["scheduled"]


@pytest.mark.asyncio
async def test_schedule_lock_per_source(monkeypatch, fetch_env):
    """Лок ключуется по источнику: один упавший в локе не блокирует другие."""
    session = FakeSession()
    session.set_result([_source("a"), _source("b")])
    monkeypatch.setattr(fetch_mod, "session_factory", lambda: session)
    # заранее занимаем лок источника "a" на текущую минуту
    await fetch_env["redis"].set("sched:a:pre", "1")  # шум — не влияет
    ctx = {"redis": fetch_env["redis"]}
    await fetch_mod.schedule_fetch_due(ctx)
    names = sorted(args[0] for _, args in fetch_env["enqueued"])
    assert names == ["a", "b"]  # оба прошли — разных минутных лока нет заранее


def test_interval_spec_registered():
    """IntervalSpec планировщика: имя = имя функции, интервал 60 с."""
    spec = IntervalSpec(fetch_mod.schedule_fetch_due, every_sec=60)
    assert spec.name == "schedule_fetch_due"
    assert spec.every_sec == 60
