"""Тесты AdapterRegistry (ТЗ п. 6.5)."""

from types import SimpleNamespace
from typing import Any

from app.adapters.common.http import HttpClient
from app.adapters.encar import EncarApiAdapter
from app.adapters.registry import AdapterRegistry


class FakeResult:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalars(self) -> "FakeResult":
        return self

    def all(self) -> list[Any]:
        return self._rows


class FakeSession:
    """Стаб AsyncSession: отдаёт строки `sources` из переданного списка."""

    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    async def __aenter__(self) -> "FakeSession":
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None

    async def execute(self, stmt: Any) -> FakeResult:
        return FakeResult(self._rows)


class FakeSecretStore:
    def __init__(self, secrets: dict[str, str] | None = None) -> None:
        self.secrets = secrets or {}

    async def get(self, ref: str) -> str | None:
        return self.secrets.get(ref)


def _source_row(name: str = "encar", adapter_class: str = "EncarApiAdapter", **kw) -> Any:
    defaults = dict(
        name=name,
        adapter_class=adapter_class,
        kind="api",
        enabled=True,
        priority=100,
        rate_limit_per_min=60,
        timeout_sec=30,
        config={"page_size": 5},
        credentials_ref=None,
        capabilities={"country": "KR"},
    )
    defaults.update(kw)
    return SimpleNamespace(**defaults)


async def test_registry_loads_enabled_sources():
    registry = AdapterRegistry(lambda: FakeSession([_source_row()]))
    await registry.load()

    adapter = registry.get("encar")
    assert isinstance(adapter, EncarApiAdapter)
    assert adapter.source_name == "encar"
    assert adapter.config == {"page_size": 5}
    assert adapter.capabilities == {"country": "KR"}
    assert registry.all() == [adapter]


async def test_registry_skips_unknown_adapter_class():
    rows = [_source_row(), _source_row(name="ghost", adapter_class="NopeAdapter")]
    registry = AdapterRegistry(lambda: FakeSession(rows))
    await registry.load()

    assert registry.all() == [registry.get("encar")]


async def test_registry_resolves_credentials_ref():
    rows = [_source_row(credentials_ref="ENCAR_API_KEY")]
    registry = AdapterRegistry(
        lambda: FakeSession(rows), secret_store=FakeSecretStore({"ENCAR_API_KEY": "secret-1"})
    )
    await registry.load()
    assert registry.get("encar").credentials == "secret-1"


async def test_registry_get_unknown_raises():
    registry = AdapterRegistry(lambda: FakeSession([]))
    await registry.load()
    try:
        registry.get("nope")
        raise AssertionError("must raise KeyError")
    except KeyError:
        pass


async def test_registry_reload_replaces_cache():
    row1 = _source_row()
    registry = AdapterRegistry(lambda: FakeSession([row1]))
    await registry.load()
    first = registry.get("encar")

    await registry.reload()
    second = registry.get("encar")
    assert first is not second
    # reload закрыл старые клиенты, новые — рабочие
    assert isinstance(first.http, HttpClient)
    assert isinstance(second.http, HttpClient)
    await registry.aclose()
