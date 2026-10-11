"""Общие фикстуры тестов."""

from datetime import datetime, timezone
from typing import Any

import pytest


class FakeRedis:
    """Мини-стаб Redis: get/set/setex/delete — для дедупликации и диалогов."""

    def __init__(self) -> None:
        self.data: dict[str, str] = {}
        self.ttls: dict[str, int] = {}

    async def set(self, key: str, value: str, nx: bool = False, ex: int | None = None):
        if nx and key in self.data:
            return None
        self.data[key] = value
        if ex:
            self.ttls[key] = ex
        return True

    async def setex(self, key: str, ttl: int, value: str) -> bool:
        self.data[key] = value
        self.ttls[key] = ttl
        return True

    async def get(self, key: str):
        return self.data.get(key)

    async def mget(self, *keys: str) -> list[str | None]:
        return [self.data.get(k) for k in keys]

    async def delete(self, *keys: str) -> int:
        removed = 0
        for key in keys:
            if key in self.data:
                del self.data[key]
                removed += 1
        return removed

    async def keys(self, pattern: str = "*") -> list[str]:
        if pattern == "*":
            return list(self.data)
        # поддержка префиксных масок вида "digest:*"
        prefix = pattern.rstrip("*")
        return [k for k in self.data if k.startswith(prefix)]

    async def rpush(self, key: str, *values: str) -> int:
        self.data.setdefault(key, [])
        self.data[key].extend(values)  # список хранится как list[str]
        return len(self.data[key])

    async def lrange(self, key: str, start: int, stop: int) -> list[str]:
        data = self.data.get(key) or []
        if stop == -1:
            return list(data[start:])
        return list(data[start : stop + 1])

    async def llen(self, key: str) -> int:
        return len(self.data.get(key) or [])

    async def incr(self, key: str) -> int:
        """Инкремент счётчика (rate limit партнёрского API)."""
        self.data[key] = int(self.data.get(key, 0)) + 1
        return int(self.data[key])

    async def expire(self, key: str, ttl: int) -> bool:
        if key in self.data:
            self.ttls[key] = ttl
            return True
        return False


class FakeSession:
    """Стаб AsyncSession: add() копит объекты, get/execute — пусто.

    execute() возвращает заглушку результата; строки можно задать через
    set_result([...]) — под select-запросы в тестах.
    """

    def __init__(self) -> None:
        self.added: list[Any] = []
        self.executed: list[Any] = []
        self.committed = False
        self.deleted: list[Any] = []
        self._rows: list[Any] = []
        self._row_queue: list[list[Any]] = []
        self.executed_params: list[Any] = []

    def set_result(self, rows: list[Any]) -> None:
        """Один результат для всех последующих execute()."""
        self._rows = rows
        self._row_queue = []

    def set_results(self, groups: list[list[Any]]) -> None:
        """Результаты на несколько последовательных execute()
        (последняя группа — на все остальные)."""
        self._row_queue = [list(group) for group in groups]
        self._rows = groups[-1] if groups else []

    def add(self, obj: Any) -> None:
        # Имитация серверных дефолтов БД (после flush появились бы id/created_at)
        if hasattr(obj, "id") and getattr(obj, "id", None) is None:
            obj.id = len(self.added) + 1
        if hasattr(obj, "created_at") and getattr(obj, "created_at", None) is None:
            obj.created_at = datetime.now(timezone.utc)
        self.added.append(obj)

    async def get(self, model: Any, pk: Any) -> Any:
        # Ищем среди добавленных через add() (имитация flush в тестовой сессии)
        for obj in self.added:
            if obj.__class__ is model and (
                getattr(obj, "id", None) == pk
                or getattr(obj, "telegram_id", None) == pk
                or getattr(obj, "source_name", None) == pk
                or getattr(obj, "name", None) == pk  # Source.name и др. строковые PK
            ):
                return obj
        return None

    async def execute(self, stmt: Any, params: Any = None) -> Any:
        self.executed.append(stmt)
        if params is not None:
            self.executed_params.append(params)
        rows = self._row_queue.pop(0) if self._row_queue else self._rows

        class _Result:
            def scalars(self) -> "_Result":
                return self

            def all(self) -> list:
                return list(rows)

            def scalar_one_or_none(self) -> Any:
                return rows[0] if rows else None

            def scalar_one(self) -> Any:
                # для count-запросов: число строк-результатов
                return len(rows)

        return _Result()

    async def commit(self) -> None:
        self.committed = True

    async def flush(self) -> None:
        return None

    async def refresh(self, obj: Any) -> None:
        return None

    async def delete(self, obj: Any) -> None:
        self.deleted.append(obj)

    async def rollback(self) -> None:
        return None

    async def close(self) -> None:
        return None


@pytest.fixture
def fake_redis() -> FakeRedis:
    return FakeRedis()


@pytest.fixture
def fake_session() -> FakeSession:
    return FakeSession()

