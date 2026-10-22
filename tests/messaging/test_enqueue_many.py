"""TECHDEBT 13.8: enqueue_many — bounded параллелизм постановки задач."""

from collections.abc import Iterable

from app.infrastructure.messaging.legacy_worker import TaskPublisher


class _RecordingPublisher(TaskPublisher):
    """Подменяет enqueue: следит за пиком одновременных publishes."""

    def __init__(self):
        super().__init__("amqp://fake")
        self.inflight = 0
        self.peak = 0
        self.ids = iter(f"id-{i}" for i in range(10000))

    async def enqueue(self, job, *args, **kwargs):  # type: ignore[override]
        self.inflight += 1
        self.peak = max(self.peak, self.inflight)
        try:
            import asyncio

            await asyncio.sleep(0.001)
            return next(self.ids)
        finally:
            self.inflight -= 1


async def test_enqueue_many_bounds_inflight():
    pub = _RecordingPublisher()
    ids = await pub.enqueue_many("send_listing_notification", [(i,) for i in range(200)], max_inflight=10)
    assert len(ids) == 200
    assert pub.peak <= 10  # никогда не больше max_inflight одновременно


async def test_enqueue_many_empty():
    pub = _RecordingPublisher()
    assert await pub.enqueue_many("job", []) == []


async def test_enqueue_many_accepts_iterable():
    pub = _RecordingPublisher()
    ids = await pub.enqueue_many("job", (x for x in [(1,), (2,)]))
    assert ids == ["id-0", "id-1"]


def _unused_iterable_guard(it: Iterable[tuple]) -> Iterable[tuple]:
    return it