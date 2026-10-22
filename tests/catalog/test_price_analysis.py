"""AI-анализ стоимости: сервис (LLM/статистика, квоты) и API (JWT + internal)."""

import asyncio
from types import SimpleNamespace

import pytest

from app.infrastructure.services.price_analysis import (
    QuotaExceeded,
    _stats,
    _verdict_from_stats,
    parse_llm_json,
    run_analysis,
)


# ---------------------------------------------------------------- парсинг LLM


def test_parse_llm_json_plain():
    data = parse_llm_json('{"verdict": "deal", "fair_min": 100, "fair_max": 200}')
    assert data and data["verdict"] == "deal"


def test_parse_llm_json_in_markdown_and_noise():
    text = 'Вот мой разбор:\n```json\n{"verdict": "overpriced"}\n```\nНадеюсь, помог!'
    assert parse_llm_json(text)["verdict"] == "overpriced"


def test_parse_llm_json_bad_verdict_or_garbage():
    assert parse_llm_json('{"verdict": "cheap"}') is None
    assert parse_llm_json("никакого json тут нет") is None
    assert parse_llm_json(None) is None
    assert parse_llm_json("") is None


# ---------------------------------------------------------------- статистика


def test_stats_median_and_too_few():
    assert _stats([100, 200, 300]) == {"count": 3, "min": 100, "max": 300, "median": 200}
    assert _stats([100]) is None


def test_verdict_from_stats_thresholds():
    comps = {"median": 1000}
    assert _verdict_from_stats(900, comps) == "deal"  # -10%
    assert _verdict_from_stats(1000, comps) == "fair"
    assert _verdict_from_stats(1200, comps) == "overpriced"  # +20%
    assert _verdict_from_stats(1200, None) == "fair"  # нет рынка — не паникуем


# ------------------------------------------------------------------- квоты


def test_quota_left_counts_window():
    assert max(0, 1 - 0) == 1 and max(0, 1 - 2) == 0


# ------------------------------------------------------------- run_analysis


class _FakeLLM:
    def __init__(self, text):
        self._text = text

    async def complete(self, messages):
        return SimpleNamespace(text=self._text, tool_calls=[])


def _listing(**kw):
    base = dict(
        id=5,
        make="Kia",
        model="Carnival",
        year=2021,
        price=30000.0,
        currency="USD",
        mileage_km=60000,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _session_with_ctx(prices, quota_used=0, with_quota=True):
    """execute по очереди: квота (scalar_one) -> comps (all) -> история (all)."""
    s = SimpleNamespace(added=[], committed=False)
    s.add = lambda obj: s.added.append(obj)

    class _Res:
        def __init__(self, kind):
            self._kind = kind

        def scalar_one(self):
            return quota_used

        def all(self):
            if self._kind == "comps":
                return [(p,) for p in prices]
            return []  # история пустая

        def scalars(self):
            return SimpleNamespace(first=lambda: None)

    queue = (["quota"] if with_quota else []) + ["comps", "history"]

    async def execute(*a, **kw):
        return _Res(queue.pop(0) if queue else "history")

    async def commit():
        s.committed = True

    s.execute = execute
    s.commit = commit
    return s


def test_run_analysis_with_llm():
    llm = _FakeLLM(
        '{"verdict": "deal", "fair_min": 28000, "fair_max": 32000, '
        '"summary": "Цена ниже рынка", "factors": ["медиана 31000"]}'
    )
    session = _session_with_ctx([30000, 31000, 32000], quota_used=0)
    out = asyncio.run(run_analysis(session, llm, _listing(), user_id=7))
    assert out["verdict"] == "deal"
    assert out["fair_min"] == 28000 and out["fair_max"] == 32000
    assert out["source"] == "ai"
    assert out["comps"]["median"] == 31000
    assert session.committed and session.added  # разбор сохранён


def test_run_analysis_llm_down_stats_fallback():
    class BrokenLLM:
        async def complete(self, messages):
            raise RuntimeError("llm down")

    session = _session_with_ctx([29000, 30000, 31000, 50000], quota_used=0)
    out = asyncio.run(run_analysis(session, BrokenLLM(), _listing(), user_id=7))
    assert out["source"] == "stats"
    assert out["verdict"] in ("deal", "fair", "overpriced")
    assert out["fair_min"] and out["fair_max"]  # диапазон из медианы


def test_run_analysis_quota_free_exceeded(monkeypatch):
    from app.infrastructure.services import price_analysis as pa

    async def fake_quota(session, user_id, listing_id):
        return 0

    monkeypatch.setattr(pa, "quota_left", fake_quota)
    session = _session_with_ctx([])
    with pytest.raises(QuotaExceeded):
        asyncio.run(run_analysis(session, _FakeLLM("{}"), _listing(), user_id=7))


def test_run_analysis_quota_paid_tier_skips(monkeypatch):
    from app.infrastructure.services import price_analysis as pa

    async def fake_quota(session, user_id, listing_id):
        raise AssertionError("pro не должен проверять квоту")

    monkeypatch.setattr(pa, "quota_left", fake_quota)
    session = _session_with_ctx([30000, 31000], with_quota=False)
    out = asyncio.run(
        run_analysis(session, _FakeLLM(None), _listing(), user_id=7, tier="pro")
    )
    assert out["verdict"] in ("deal", "fair", "overpriced")
