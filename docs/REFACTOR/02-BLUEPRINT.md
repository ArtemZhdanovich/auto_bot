# 02. Целевая архитектура и код по слоям

Документ описывает, как разложить существующий `app/` на Clean Architecture
с Dishka и FastStream, **не переписывая бизнес-логику заново**. Все примеры
используют реальные имена проекта (`Listing`, `Favorite`, `Lead`,
`settings.rabbitmq_url`) и совместимы с SQLAlchemy 2.0 async, Pydantic 2,
Dishka 1.x, FastStream 0.5+.

---

## 1. Правило зависимостей

```
        transport/http     transport/messaging     transport/bot
                \                  |                  /
                 \                 |                 /
                  ─────────► application ──────────►
                                  |
                                  ▼
                                domain
                                  ▲
                                  │ implements ports
                            infrastructure
                                  ▲
                                  │ wiring only
                                 di
```

Формально:

- `domain` не импортирует **ничего**, кроме стандартной библиотеки.
- `application` импортирует `domain` и свои порты. Не знает про FastAPI,
  SQLAlchemy, RabbitMQ, aiogram.
- `infrastructure` импортирует `domain` (реализует порты) и внешние
  библиотеки.
- `transport` импортирует `application` и схемы; не знает про `Session`.
- `di` — единственное место, где встречаются все слои. Это composition root.

Проверяется автоматически (CI):

```bash
ruff check --select TID app/domain app/application   # запрет «внешних» импортов
# или import-linter:
# [importlinter:contract:layers]
# layers = app.transport | app.infrastructure | app.application | app.domain
```

---

## 2. Целевое дерево

Пакет остаётся `app/` (не `src/`): переезд на `src/` — это правка импортов в
111 файлах без архитектурной выгоды. Внутри — слой первый, контекст второй:

```text
app/
├── domain/                          # чистый Python, stdlib only
│   ├── shared/
│   │   ├── errors.py                # DomainError, NotFound, Forbidden, Conflict
│   │   ├── events.py                # DomainEvent (frozen dataclass)
│   │   └── value_objects.py         # Money, Currency, Cursor
│   ├── listings/
│   │   ├── entity.py                # Listing (только поля и правила)
│   │   ├── errors.py
│   │   └── ports.py                 # ListingRepository, ListingQuery
│   ├── favorites/
│   │   ├── entity.py
│   │   ├── errors.py
│   │   └── ports.py                 # FavoriteRepository
│   ├── leads/
│   │   ├── entity.py                # Lead + pull_events()
│   │   ├── events.py                # LeadCreated, LeadClaimed
│   │   ├── errors.py
│   │   └── ports.py
│   ├── partners/
│   │   ├── entity.py                # PartnerAccount (баланс, квота)
│   │   ├── errors.py
│   │   └── ports.py
│   └── dialog/
│       ├── entity.py
│       └── ports.py
│
├── application/                     # use cases / interactors
│   ├── shared/
│   │   ├── dto.py                   # dataclass-DTO (не Pydantic)
│   │   ├── pagination.py            # Cursor, Page[T]
│   │   └── ports.py                 # UnitOfWork, EventPublisher, IdempotencyGuard, Clock
│   ├── favorites/
│   │   ├── commands.py
│   │   └── use_cases.py             # ToggleFavorite, ListFavorites
│   ├── listings/
│   │   ├── queries.py
│   │   └── use_cases.py             # ListListings, GetListing, GetFacets
│   ├── leads/
│   │   └── use_cases.py             # CreateLead, UpdateLeadStatus, GetLeadForPartner
│   ├── partners/
│   │   └── use_cases.py             # ClaimLead, TopUp
│   └── dialog/
│       └── use_cases.py
│
├── infrastructure/
│   ├── db/
│   │   ├── models/                  # SQLAlchemy: users.py, listings.py, ...
│   │   ├── dao/                     # BaseDAO + доменные DAO (SQL живёт здесь)
│   │   ├── repositories/            # реализация domain.ports
│   │   ├── mappers.py               # ORM ↔ domain entity
│   │   ├── uow.py                   # SqlAlchemyUnitOfWork (+ outbox)
│   │   └── outbox.py                # OutboxMessage, OutboxDAO
│   ├── messaging/
│   │   ├── topology.py              # exchange/queue/bindings
│   │   ├── publisher.py             # FastStreamEventPublisher
│   │   ├── relay.py                 # OutboxRelay
│   │   ├── idempotency.py           # RedisIdempotencyGuard
│   │   └── broker.py                # создание RabbitBroker
│   ├── security/
│   │   ├── jwt.py
│   │   ├── passwords.py
│   │   ├── internal_token.py
│   │   └── rate_limit.py
│   ├── sources/                     # бывшие app/adapters
│   │   ├── base.py, encar.py, kcar.py, che168.py, registry.py
│   │   └── common/{http.py, proxy.py, normalize.py, dedup.py}
│   ├── ai/                          # бывшие app/ai
│   ├── storage/s3.py
│   └── cache/redis.py
│
├── transport/
│   ├── http/
│   │   ├── routers/                 # бывшие app/api/*.py
│   │   ├── schemas/                 # бывшие app/schemas (вход/выход)
│   │   ├── errors.py                # DomainError → HTTP
│   │   └── middleware/{request_id.py, rate_limit.py}
│   ├── messaging/
│   │   ├── contracts.py             # Pydantic-модели сообщений
│   │   └── handlers/                # бывшие app/workers/*
│   └── bot/                         # бывшие app/bot/*
│
├── di/
│   ├── providers/{settings,database,redis,broker,repositories,use_cases,security}.py
│   └── container.py                 # make_api_container / make_worker_container / make_bot_container
│
├── app_factory.py                   # create_app(container=None) — для тестов
├── main_api.py                      # uvicorn app.main_api:app
├── main_worker.py                   # python -m app.main_worker
└── main_bot.py                      # python -m app.main_bot
```

### Маппинг «было → станет»

| Сейчас | Куда | Как |
|---|---|---|
| `app/api/*.py` | `transport/http/routers/*` + `application/*/use_cases.py` | SQL и правила вынимаются в use case/DAO |
| `app/services/*.py` | `domain/*` (правила) + `application/*` (сценарии) + `infrastructure/*` (I/O) | по одному модулю, не пакетом |
| `app/adapters/*` | `infrastructure/sources/*` | переименование + реестр через DI |
| `app/ai/*` | `infrastructure/ai/*` + `application/dialog` | цикл агента остаётся в infra |
| `app/bot/*` | `transport/bot/*` | без изменений логики |
| `app/queue/rabbit.py` | `infrastructure/messaging/*` | заменяется FastStream, не оборачивается |
| `app/db/models.py` | `infrastructure/db/models/*` | разбить по контекстам, добавить `lazy="raise"` при связях |
| `app/schemas/*` | `transport/http/schemas/*` + `application/*/dto.py` | разделить вход/выход и DTO |
| `app/core/config.py` | `app/config.py` (остаётся) | Settings — это composition root |
| `app/core/deps*.py` | `infrastructure/security/*` + `di/providers/*` | три механизма → три провайдера |
| `app/main.py` | `app_factory.py` + `main_api.py` | убрать второй `FastAPI()` |

---

## 3. Домен

### 3.1. События

```python
# app/domain/shared/events.py
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid4


def now_utc() -> datetime:
    """Текущее время в UTC (единая точка для тестов)."""
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True, kw_only=True)
class DomainEvent:
    """Базовое доменное событие.

    Attributes:
        event_id: Уникальный идентификатор события (ключ идемпотентности).
        occurred_at: Момент возникновения.
    """

    event_id: UUID = field(default_factory=uuid4)
    occurred_at: datetime = field(default_factory=now_utc)


@dataclass(frozen=True, slots=True, kw_only=True)
class LeadClaimed(DomainEvent):
    lead_id: int
    partner_id: int
    amount: str  # Decimal сериализуется строкой — без потери точности
    currency: str
```

### 3.2. Сущность с поведением

```python
# app/domain/leads/entity.py
from dataclasses import dataclass, field
from decimal import Decimal

from app.domain.leads.errors import LeadAlreadyClaimed, LeadNotClaimable
from app.domain.leads.events import LeadClaimed
from app.domain.shared.events import DomainEvent


@dataclass(slots=True)
class Lead:
    """Лид на покупку автомобиля.

    Инварианты:
        - claim возможен только из статуса new/in_progress;
        - один лид принадлежит максимум одному партнёру;
        - при claim баланс партнёра не может уйти в минус (проверка в use case).
    """

    id: int
    user_id: int
    status: str
    partner_id: int | None = None
    listing_id: int | None = None
    _events: list[DomainEvent] = field(default_factory=list, repr=False)

    def claim(self, partner_id: int, cost: Decimal, currency: str) -> None:
        """Закрепить лид за партнёром.

        Args:
            partner_id: ID партнёрского аккаунта.
            cost: Стоимость лида.
            currency: Валюта списания.

        Raises:
            LeadAlreadyClaimed: Лид уже забран другим партнёром.
            LeadNotClaimable: Статус не допускает claim.
        """
        if self.partner_id is not None:
            raise LeadAlreadyClaimed(self.id, self.partner_id)
        if self.status not in {"new", "in_progress"}:
            raise LeadNotClaimable(self.id, self.status)

        self.partner_id = partner_id
        self._events.append(
            LeadClaimed(
                lead_id=self.id,
                partner_id=partner_id,
                amount=str(cost),
                currency=currency,
            )
        )

    def pull_events(self) -> list[DomainEvent]:
        """Забрать накопленные события (агрегат их больше не отдаст)."""
        events, self._events = self._events, []
        return events
```

### 3.3. Порты

```python
# app/domain/favorites/ports.py
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True, slots=True)
class FavoriteCursor:
    """Позиция в keyset-пагинации избранного."""

    created_at: datetime
    listing_id: int


@dataclass(frozen=True, slots=True)
class FavoriteItem:
    """Элемент списка избранного: объявление + первая цена."""

    listing_id: int
    first_price: str | None
    created_at: datetime


class FavoriteRepository(Protocol):
    """Хранилище избранного. Реализация — infrastructure/db/repositories."""

    async def toggle(self, user_id: int, listing_id: int) -> bool:
        """Атомарно переключить состояние. Возвращает итоговое `favorited`."""

    async def page(
        self,
        user_id: int,
        *,
        limit: int,
        cursor: FavoriteCursor | None,
    ) -> tuple[list[FavoriteItem], bool]:
        """Страница избранного и признак «есть ещё»."""
```

---

## 4. Прикладной слой

### 4.1. Порты приложения

```python
# app/application/shared/ports.py
from types import TracebackType
from typing import Protocol, Self

from app.domain.shared.events import DomainEvent


class UnitOfWork(Protocol):
    """Граница транзакции на один HTTP-запрос или одно сообщение."""

    async def __aenter__(self) -> Self: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...

    def collect(self, *events: DomainEvent) -> None:
        """Положить события в outbox в рамках текущей транзакции."""

    async def commit(self) -> None:
        """Зафиксировать данные и события одной транзакцией."""

    async def rollback(self) -> None: ...


class EventPublisher(Protocol):
    """Публикация события в брокер.

    `publish` может только буферизовать событие; `flush` отправляет буфер.
    UoW вызывает `flush` после успешного commit, поэтому порядок
    «сначала БД, потом брокер» обеспечивается в одном месте.
    """

    async def publish(self, event: DomainEvent, routing_key: str) -> None: ...

    async def flush(self) -> None: ...


class IdempotencyGuard(Protocol):
    """Защита от повторной обработки сообщения."""

    async def acquire(self, key: str, ttl_sec: int) -> bool: ...

    async def release(self, key: str) -> None: ...
```

### 4.2. Use case: toggle избранного

```python
# app/application/favorites/use_cases.py
from dataclasses import dataclass

from app.application.shared.dto import FavoriteState
from app.application.shared.pagination import Page
from app.application.shared.ports import UnitOfWork
from app.domain.favorites.ports import FavoriteRepository
from app.domain.listings.errors import ListingNotFound
from app.domain.listings.ports import ListingRepository


@dataclass(frozen=True, slots=True)
class ToggleFavoriteCommand:
    user_id: int
    listing_id: int


class ToggleFavorite:
    """Переключить избранное.

    Владение проверяется здесь, а не в роутере: `user_id` приходит из
    аутентификации, `listing_id` — из пути. Роутер не имеет доступа к БД.
    """

    def __init__(
        self,
        uow: UnitOfWork,
        listings: ListingRepository,
        favorites: FavoriteRepository,
    ) -> None:
        self._uow = uow
        self._listings = listings
        self._favorites = favorites

    async def execute(self, cmd: ToggleFavoriteCommand) -> FavoriteState:
        async with self._uow:
            if not await self._listings.exists_active(cmd.listing_id):
                raise ListingNotFound(cmd.listing_id)
            favorited = await self._favorites.toggle(cmd.user_id, cmd.listing_id)
            await self._uow.commit()
        return FavoriteState(favorited=favorited)
```

### 4.3. Use case: keyset-пагинация

```python
# app/application/shared/pagination.py
from dataclasses import dataclass
from typing import Generic, TypeVar

from app.domain.favorites.ports import FavoriteCursor

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class Page(Generic[T]):
    """Страница результатов + курсор следующей."""

    items: list[T]
    has_more: bool
    next_cursor: str | None = None


class CursorCodec:
    """Base64(JSON) — курсор не должен быть предсказуемым для клиента."""

    @staticmethod
    def encode(payload: dict) -> str: ...

    @staticmethod
    def decode_favorite(raw: str) -> FavoriteCursor: ...
```

```python
# app/application/favorites/use_cases.py (продолжение)
@dataclass(frozen=True, slots=True)
class ListFavoritesQuery:
    user_id: int
    limit: int
    cursor: FavoriteCursor | None


class ListFavorites:
    """Страница избранного без OFFSET.

    Курсор кодирует `(created_at, listing_id)` последней строки. При
    сортировке по `created_at DESC` условие — `(created_at, listing_id) <
    (cursor.created_at, cursor.listing_id)`.
    """

    def __init__(self, uow: UnitOfWork, favorites: FavoriteRepository) -> None:
        self._uow = uow
        self._favorites = favorites

    async def execute(self, query: ListFavoritesQuery) -> Page[FavoriteItem]:
        async with self._uow:
            items, has_more = await self._favorites.page(
                query.user_id, limit=query.limit, cursor=query.cursor
            )
        return Page(items=items, has_more=has_more, next_cursor=...)
```

### 4.4. Use case: IDOR-проверка

```python
# app/application/leads/use_cases.py
class GetLeadForPartner:
    """Отдать лид партнёру — только если он ему принадлежит.

    Проверка владения внутри use case: роутер не может её «забыть»,
    а тест проверяет сценарий без HTTP.
    """

    def __init__(self, uow: UnitOfWork, leads: LeadRepository) -> None:
        self._uow = uow
        self._leads = leads

    async def execute(self, lead_id: int, partner_id: int) -> LeadView:
        async with self._uow:
            lead = await self._leads.get(lead_id)
        if lead is None or lead.partner_id != partner_id:
            # 404, а не 403: не раскрываем существование чужого лида.
            raise LeadNotFound(lead_id)
        return LeadView.from_entity(lead)
```

---

## 5. Инфраструктура

### 5.1. Модели и `lazy="raise"`

В текущем `models.py` **нет ни одного `relationship()`**, поэтому `lazy="raise"`
применять не к чему. Если связи добавляются (например, `Favorite.listing`),
правило такое:

```python
class Favorite(Base):
    __tablename__ = "favorites"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.telegram_id"), primary_key=True)
    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.id"), primary_key=True)

    listing: Mapped["Listing"] = relationship(
        lazy="raise",  # случайный lazy-load в async → ImmediateLoadingError, а не MissingGreenlet
        back_populates="favorites",
    )
```

Загрузка — только явная, в DAO:

```python
stmt = select(Favorite).options(joinedload(Favorite.listing))  # many-to-one
stmt = select(Listing).options(selectinload(Listing.images))   # one-to-many
```

### 5.2. DAO: атомарный toggle одним запросом

```python
# app/infrastructure/db/dao/favorites.py
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_TOGGLE_SQL = text(
    """
    WITH ins AS (
        INSERT INTO favorites (user_id, listing_id)
        VALUES (:user_id, :listing_id)
        ON CONFLICT (user_id, listing_id) DO NOTHING
        RETURNING 1
    ), del AS (
        DELETE FROM favorites
        WHERE user_id = :user_id
          AND listing_id = :listing_id
          AND NOT EXISTS (SELECT 1 FROM ins)
        RETURNING 1
    )
    SELECT EXISTS (SELECT 1 FROM ins) AS favorited
    """
)


class FavoriteDAO:
    """SQL для избранного. Единственное место, где живёт текст запросов.

    Здесь raw SQL оправдан: toggle — один атомарный statement с двумя
    data-modifying CTE. ORM-версия развалилась бы на несколько запросов.
    Параметры — только bind, без f-string.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def toggle(self, user_id: int, listing_id: int) -> bool:
        """Атомарный toggle: без read-then-write и без 500 на гонке."""
        result = await self._session.execute(
            _TOGGLE_SQL, {"user_id": user_id, "listing_id": listing_id}
        )
        return bool(result.scalar_one())
```

> Замечание по контракту: toggle по своей природе не идемпотентен. Для
> API v2 стоит добавить `PUT /favorites/{id}` (идемпотентно добавить) и
> `DELETE` (идемпотентно убрать), оставив `POST .../favorite` как есть для
> совместимости с Mini App.

### 5.3. Keyset-пагинация в репозитории

```python
# app/infrastructure/db/repositories/favorites.py
from sqlalchemy import select, tuple_

class SqlAlchemyFavoriteRepository(FavoriteRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._dao = FavoriteDAO(session)

    async def toggle(self, user_id: int, listing_id: int) -> bool:
        return await self._dao.toggle(user_id, listing_id)

    async def page(
        self, user_id: int, *, limit: int, cursor: FavoriteCursor | None
    ) -> tuple[list[FavoriteItem], bool]:
        # DISTINCT ON даёт ровно одну строку истории на объявление.
        first_price = (
            select(ListingPriceHistory.listing_id, ListingPriceHistory.price)
            .distinct(ListingPriceHistory.listing_id)
            .order_by(
                ListingPriceHistory.listing_id,
                ListingPriceHistory.recorded_at,
                ListingPriceHistory.id,
            )
            .cte("first_price")
        )
        stmt = (
            select(Favorite.listing_id, Favorite.created_at, first_price.c.price)
            .join(first_price, first_price.c.listing_id == Favorite.listing_id, isouter=True)
            .where(Favorite.user_id == user_id)
            .order_by(Favorite.created_at.desc(), Favorite.listing_id.desc())
            .limit(limit + 1)
        )
        if cursor is not None:
            stmt = stmt.where(
                tuple_(Favorite.created_at, Favorite.listing_id)
                < tuple_(cursor.created_at, cursor.listing_id)
            )
        rows = (await self._session.execute(stmt)).all()
        has_more = len(rows) > limit
        return [self._to_item(row) for row in rows[:limit]], has_more
```

Индекс под запрос (миграция):

```sql
CREATE INDEX CONCURRENTLY idx_favorites_user_created_id
    ON favorites (user_id, created_at DESC, listing_id DESC);
```

### 5.4. Unit of Work + outbox

```python
# app/infrastructure/db/uow.py
from types import TracebackType

from sqlalchemy.ext.asyncio import AsyncSession

from app.application.shared.ports import UnitOfWork
from app.domain.shared.events import DomainEvent
from app.infrastructure.db.mappers import routing_key_for
from app.infrastructure.db.outbox import OutboxDAO


class SqlAlchemyUnitOfWork(UnitOfWork):
    """Одна транзакция на запрос/сообщение.

    `commit()` делает flush данных, затем либо кладёт накопленные события в
    outbox **той же транзакцией**, либо отправляет буфер сразу после commit.
    Если commit упал — события не уйдут в брокер ни в одном из режимов.
    """

    def __init__(
        self,
        session: AsyncSession,
        outbox: OutboxDAO,
        publisher: EventPublisher,
        *,
        use_outbox: bool,
    ) -> None:
        self._session = session
        self._outbox = outbox
        self._publisher = publisher
        self._use_outbox = use_outbox
        self._events: list[DomainEvent] = []

    async def __aenter__(self) -> "SqlAlchemyUnitOfWork":
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if exc_type is not None:
            await self.rollback()

    def collect(self, *events: DomainEvent) -> None:
        self._events.extend(events)

    async def commit(self) -> None:
        await self._session.flush()
        if self._events:
            if self._use_outbox:
                # Событие в той же транзакции, что и данные.
                await self._outbox.add_all(self._events)
            else:
                # Буферизация; уйдёт только после commit ниже.
                for event in self._events:
                    await self._publisher.publish(event, routing_key_for(event))
            self._events.clear()
        await self._session.commit()
        if not self._use_outbox:
            await self._publisher.flush()  # строго после commit

    async def rollback(self) -> None:
        self._events.clear()
        await self._session.rollback()
```

```python
# app/infrastructure/db/outbox.py
from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, Integer, String, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.db.models.base import Base


class OutboxMessage(Base):
    """Транзакционный outbox: событие живёт в БД, пока не опубликовано."""

    __tablename__ = "outbox"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[UUID] = mapped_column(unique=True, nullable=False)  # ключ идемпотентности
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    routing_key: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    last_error: Mapped[str | None] = mapped_column(String(512))


class OutboxDAO:
    def __init__(self, session) -> None:
        self._session = session

    async def add_all(self, events) -> None:
        self._session.add_all(
            [
                OutboxMessage(
                    event_id=e.event_id,
                    event_type=type(e).__name__,
                    # routing_key_for/event_to_dict — маппинг событие → конверт
                    # в app/infrastructure/db/mappers.py
                    routing_key=routing_key_for(e),
                    payload=event_to_dict(e),
                    occurred_at=e.occurred_at,
                )
                for e in events
            ]
        )
```

Миграция:

```sql
CREATE TABLE outbox (
    id          BIGSERIAL PRIMARY KEY,
    event_id    UUID NOT NULL UNIQUE,
    event_type  VARCHAR(64) NOT NULL,
    routing_key VARCHAR(64) NOT NULL,
    payload     JSONB NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL,
    published_at TIMESTAMPTZ,
    attempts    INTEGER NOT NULL DEFAULT 0,
    last_error  VARCHAR(512)
);

CREATE INDEX idx_outbox_unpublished
    ON outbox (id) WHERE published_at IS NULL;
```

### 5.5. Релей outbox

```python
# app/infrastructure/messaging/relay.py
import asyncio
import logging
from datetime import UTC, datetime

from sqlalchemy import select

from app.infrastructure.db.models.outbox import OutboxMessage

logger = logging.getLogger(__name__)


class OutboxRelay:
    """Публикует неопубликованные события. At-least-once, не exactly-once.

    Падение между `publish` и `commit` даёт повторную публикацию — поэтому
    консьюмеры обязаны быть идемпотентны по `event_id`.
    """

    def __init__(self, session_factory, publisher, batch: int = 100) -> None:
        self._session_factory = session_factory
        self._publisher = publisher
        self._batch = batch

    async def drain_once(self) -> int:
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    select(OutboxMessage)
                    .where(OutboxMessage.published_at.is_(None))
                    .order_by(OutboxMessage.id)
                    .limit(self._batch)
                    .with_for_update(skip_locked=True)
                )
            ).scalars().all()

            for row in rows:
                try:
                    await self._publisher.publish_raw(
                        routing_key=row.routing_key,
                        payload=row.payload,
                        event_id=str(row.event_id),
                    )
                    row.published_at = datetime.now(UTC)
                except Exception as exc:  # noqa: BLE001 — релей не падает
                    row.attempts += 1
                    row.last_error = str(exc)[:512]
                    logger.warning("outbox_publish_failed id=%s", row.id, exc_info=True)
            await session.commit()
            return len(rows)

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            sent = await self.drain_once()
            if sent < self._batch:
                await asyncio.sleep(0.5)
```

### 5.6. Публикация и collect/flush

Два режима — выбирать по критичности:

| Режим | Как | Гарантия | Где применять |
|---|---|---|---|
| **Collect/flush** | use case копит события в UoW; после `commit()` транспорт вызывает `publisher.flush()` | at-most-once при падении процесса между commit и publish | аналитика, cache invalidation, необязательные уведомления |
| **Outbox** | события пишутся в `outbox` той же транзакцией, релей публикует | at-least-once, событие не теряется | деньги (`charge_lead`), лиды, платежи, статусы заказов |

Реализация collect/flush (для необязательных событий):

```python
# app/infrastructure/messaging/publisher.py
class BufferedEventPublisher(EventPublisher):
    """Копит события в памяти; flush — после успешного commit."""

    def __init__(self, broker: RabbitBroker) -> None:
        self._broker = broker
        self._buffer: list[tuple[DomainEvent, str]] = []

    async def publish(self, event: DomainEvent, routing_key: str) -> None:
        self._buffer.append((event, routing_key))

    async def flush(self) -> None:
        pending, self._buffer = self._buffer, []
        for event, routing_key in pending:
            await self._broker.publish(
                event_to_dict(event),
                exchange=TASKS_EXCHANGE,
                routing_key=routing_key,
            )

    def discard(self) -> None:
        self._buffer.clear()


class RawEventPublisher(Protocol):
    """Публикация готового конверта (для outbox-релея)."""

    async def publish_raw(
        self, *, routing_key: str, payload: dict, event_id: str
    ) -> None: ...


class FastStreamRawPublisher:
    """Публикатор без буфера: APP-скоуп, живёт весь процесс."""

    def __init__(self, broker: RabbitBroker) -> None:
        self._broker = broker

    async def publish_raw(
        self, *, routing_key: str, payload: dict, event_id: str
    ) -> None:
        await self._broker.publish(
            payload,
            exchange=TASKS_EXCHANGE,
            routing_key=routing_key,
            headers={"event_id": event_id},
        )
```

Важно: `flush()` вызывается **после** `await uow.commit()` и **не** внутри
`async with uow` — иначе событие уйдёт до фиксации.

---

## 6. Dishka: провайдеры и контейнер

### 6.1. Скоупы

| Скоуп | Что живёт | Время жизни |
|---|---|---|
| `APP` | `Settings`, `AsyncEngine`, `async_sessionmaker`, `Redis`, `RabbitBroker`, `httpx.AsyncClient`, реестр адаптеров | процесс |
| `REQUEST` | `AsyncSession`, `UnitOfWork`, репозитории, use cases, `CurrentUser`, guard идемпотентности | HTTP-запрос / сообщение / telegram-update |

### 6.2. Провайдеры

```python
# app/di/providers/database.py
from collections.abc import AsyncIterator

from dishka import Provider, Scope, provide
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import Settings
from app.infrastructure.db.outbox import OutboxDAO
from app.infrastructure.db.uow import SqlAlchemyUnitOfWork


class DatabaseProvider(Provider):
    """APP: engine и фабрика сессий; REQUEST: сессия и UoW."""

    @provide(scope=Scope.APP)
    async def engine(self, settings: Settings) -> AsyncIterator[AsyncEngine]:
        engine = create_async_engine(
            settings.database_url,
            pool_pre_ping=True,
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
            pool_timeout=settings.db_pool_timeout_sec,
            pool_recycle=settings.db_pool_recycle_sec,
            pool_use_lifo=True,
            connect_args={
                "timeout": 10,
                "command_timeout": 30,
                "server_settings": {
                    "application_name": "autoselect-api",
                    "statement_timeout": "30000",
                    "lock_timeout": "5000",
                    "idle_in_transaction_session_timeout": "60000",
                },
            },
        )
        try:
            yield engine
        finally:
            await engine.dispose()

    @provide(scope=Scope.APP)
    def sessionmaker(self, engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
        return async_sessionmaker(engine, expire_on_commit=False)

    @provide(scope=Scope.REQUEST)
    async def session(
        self, sm: async_sessionmaker[AsyncSession]
    ) -> AsyncIterator[AsyncSession]:
        async with sm() as session:
            yield session

    @provide(scope=Scope.REQUEST)
    def outbox(self, session: AsyncSession) -> OutboxDAO:
        return OutboxDAO(session)

    @provide(scope=Scope.REQUEST)
    def uow(
        self,
        session: AsyncSession,
        outbox: OutboxDAO,
        publisher: EventPublisher,
        settings: Settings,
    ) -> UnitOfWork:
        return SqlAlchemyUnitOfWork(
            session,
            outbox,
            publisher,
            use_outbox=settings.use_outbox,
        )
```

```python
# app/di/providers/broker.py
from collections.abc import AsyncIterator

from dishka import Provider, Scope, provide
from faststream.rabbit import RabbitBroker

from app.config import Settings


class BrokerProvider(Provider):
    """APP: брокер и публикатор. REQUEST: буфер событий (collect/flush)."""

    @provide(scope=Scope.APP)
    def broker(self, settings: Settings) -> RabbitBroker:
        # Стартует/останавливается в entrypoint (lifespan или main_worker):
        # так брокер не поднимает соединение при импорте модуля.
        return RabbitBroker(
            settings.rabbitmq_url,
            graceful_timeout=30.0,  # даёт дождаться in-flight при stop()
        )

    @provide(scope=Scope.APP)
    def raw_publisher(self, broker: RabbitBroker) -> RawEventPublisher:
        """Публикатор для outbox-релея: без буфера и REQUEST-скоупа."""
        return FastStreamRawPublisher(broker)

    @provide(scope=Scope.REQUEST)
    def publisher(self, broker: RabbitBroker) -> EventPublisher:
        return BufferedEventPublisher(broker)
```

```python
# app/di/providers/repositories.py
class RepositoryProvider(Provider):
    """Реализации доменных портов. Ключ — протокол из `domain.ports`."""

    scope = Scope.REQUEST

    @provide
    def favorites(self, session: AsyncSession) -> FavoriteRepository:
        return SqlAlchemyFavoriteRepository(session)

    @provide
    def listings(self, session: AsyncSession) -> ListingRepository:
        return SqlAlchemyListingRepository(session)

    @provide
    def leads(self, session: AsyncSession) -> LeadRepository:
        return SqlAlchemyLeadRepository(session)
```

```python
# app/di/providers/use_cases.py
class UseCaseProvider(Provider):
    """Сценарии. Зависят только от портов, поэтому собираются автоматически."""

    scope = Scope.REQUEST

    toggle_favorite = provide(ToggleFavorite)
    list_favorites = provide(ListFavorites)
    get_lead_for_partner = provide(GetLeadForPartner)
    claim_lead = provide(ClaimLead)
```

> `provide(ToggleFavorite)` как атрибут класса — короткая форма Dishka: тип
> возврата берётся из самой фабрики. Для фабрик с зависимостями (репозитории)
> используется явный метод с аннотацией возврата.

### 6.3. Контейнер и три процесса

```python
# app/di/container.py
from dishka import AsyncContainer, make_async_container
from dishka.integrations.aiogram import AiogramProvider
from dishka.integrations.fastapi import FastapiProvider
from dishka_faststream import FastStreamProvider

from app.di.providers import (
    BrokerProvider,
    DatabaseProvider,
    RedisProvider,
    RepositoryProvider,
    SecurityProvider,
    SettingsProvider,
    UseCaseProvider,
)

_CORE = (
    SettingsProvider(),
    DatabaseProvider(),
    RedisProvider(),
    BrokerProvider(),
    RepositoryProvider(),
    UseCaseProvider(),
    SecurityProvider(),
)


def make_api_container() -> AsyncContainer:
    return make_async_container(*_CORE, FastapiProvider())


def make_worker_container() -> AsyncContainer:
    return make_async_container(*_CORE, FastStreamProvider())


def make_bot_container() -> AsyncContainer:
    return make_async_container(*_CORE, AiogramProvider())
```

> Один и тот же набор провайдеров в трёх процессах — это и есть выгода
> Dishka: `Settings`, `AsyncEngine`, `RabbitBroker` и use cases описаны
> один раз. Различаются только интеграционные провайдеры (контекст события).

---

## 7. Транспорт HTTP

### 7.1. Роутер

```python
# app/transport/http/routers/favorites.py
from typing import Annotated

from dishka.integrations.fastapi import DishkaRoute, FromDishka
from fastapi import APIRouter, Path, Query

from app.application.favorites.use_cases import (
    ListFavorites,
    ListFavoritesQuery,
    ToggleFavorite,
    ToggleFavoriteCommand,
)
from app.transport.http.schemas.favorites import FavoritePageOut, FavoriteStateOut

router = APIRouter(prefix="/api", tags=["favorites"], route_class=DishkaRoute)


@router.get("/favorites", response_model=FavoritePageOut)
async def list_favorites(
    use_case: FromDishka[ListFavorites],
    user: FromDishka[CurrentUser],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: str | None = None,
) -> FavoritePageOut:
    """Избранное пользователя (keyset-пагинация)."""
    page = await use_case.execute(
        ListFavoritesQuery(
            user_id=user.id,
            limit=limit,
            cursor=CursorCodec.decode_favorite(cursor) if cursor else None,
        )
    )
    return FavoritePageOut.from_page(page)


@router.post("/listings/{listing_id}/favorite", response_model=FavoriteStateOut)
async def toggle_favorite(
    use_case: FromDishka[ToggleFavorite],
    user: FromDishka[CurrentUser],
    listing_id: Annotated[int, Path(ge=1)],
) -> FavoriteStateOut:
    """Toggle «В избранное» (контракт сохранён)."""
    state = await use_case.execute(
        ToggleFavoriteCommand(user_id=user.id, listing_id=listing_id)
    )
    return FavoriteStateOut(favorited=state.favorited)
```

`CurrentUser` — не ORM-модель, а DTO приложения:

```python
# app/application/shared/dto.py
@dataclass(frozen=True, slots=True)
class CurrentUser:
    id: int
    role: str
    is_active: bool
```

Провайдер аутентификации (REQUEST) читает JWT из `Request` и возвращает DTO;
`User` из БД не покидает инфраструктуру.

### 7.2. Ошибки

```python
# app/transport/http/errors.py
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.domain.shared.errors import Conflict, DomainError, Forbidden, NotFound

_STATUS = {NotFound: 404, Forbidden: 403, Conflict: 409}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(DomainError)
    async def domain_error_handler(request: Request, exc: DomainError) -> JSONResponse:
        """Доменные ошибки → 4xx без стектрейса и внутренних деталей."""
        code = _STATUS.get(type(exc), 400)
        return JSONResponse(
            status_code=code,
            content={"code": exc.code, "detail": exc.public_message},
        )
```

Правило: `HTTPException` остаётся только в transport (валидация пути,
аутентификация). Use case бросает доменные исключения.

### 7.3. Схемы

```python
# app/transport/http/schemas/favorites.py
from pydantic import BaseModel, ConfigDict, Field


class ToggleFavoriteIn(BaseModel):
    """Входная схема: лишние поля — 422, а не «проглотить»."""

    model_config = ConfigDict(extra="forbid")


class FavoriteStateOut(BaseModel):
    favorited: bool


class FavoriteCardOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    first_price: float | None = None
    title: str | None = None


class FavoritePageOut(BaseModel):
    items: list[FavoriteCardOut]
    next_cursor: str | None = None
    has_more: bool = False

    @classmethod
    def from_page(cls, page) -> "FavoritePageOut":
        return cls(
            items=[
                FavoriteCardOut(id=item.listing_id, first_price=item.first_price)
                for item in page.items
            ],
            next_cursor=page.next_cursor,
            has_more=page.has_more,
        )
```

### 7.4. Сборка приложения и lifespan

```python
# app/app_factory.py
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from dishka import AsyncContainer
from dishka.integrations.fastapi import setup_dishka
from fastapi import FastAPI
from faststream.rabbit import RabbitBroker

from app.di.container import make_api_container


def create_app(container: AsyncContainer | None = None) -> FastAPI:
    """Собрать приложение.

    Фабрика, а не модульный синглтон: тесты передают контейнер с fake-провайдерами.
    """
    container = container or make_api_container()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        broker: RabbitBroker = await container.get(RabbitBroker)
        await broker.start()   # publish only: без подписок
        yield
        await broker.stop()    # graceful_timeout дожидается in-flight publish
        await container.close()

    app = FastAPI(
        title="Auto Selection Ecosystem API",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.include_router(favorites.router)
    app.include_router(listings.router)
    # ... остальные роутеры
    setup_dishka(container=container, app=app)
    return app


# app/main_api.py
from app.app_factory import create_app

app = create_app()
```

> `await container.get(...)` допустим только для APP-скоупа и только в
> composition root. В хендлерах — `FromDishka[...]`.

---

## 8. Транспорт сообщений (FastStream)

### 8.1. Топология

Сохраняем имена очередей — это позволяет катить новый воркер рядом со старым.

```python
# app/infrastructure/messaging/topology.py
from faststream.rabbit import ExchangeType, RabbitExchange, RabbitQueue

TASKS_EXCHANGE = RabbitExchange("tasks", type=ExchangeType.DIRECT, durable=True)
DLX_EXCHANGE = RabbitExchange("tasks.dlx", type=ExchangeType.FANOUT, durable=True)
DEAD_LETTER_QUEUE = RabbitQueue("dead_letter", durable=True)

# Quorum-очередь: x-delivery-limit сам отправляет «ядовитое» сообщение в DLX
# после N доставок. Требует RabbitMQ >= 3.12.
FETCH_QUEUE = RabbitQueue(
    "fetch",
    durable=True,
    routing_key="fetch_source_task",
    arguments={
        "x-queue-type": "quorum",
        "x-delivery-limit": 5,
        "x-dead-letter-exchange": "tasks.dlx",
    },
)
SEND_QUEUE = RabbitQueue(
    "send",
    durable=True,
    routing_key="send_listing_notification",
    arguments={
        "x-queue-type": "quorum",
        "x-delivery-limit": 5,
        "x-dead-letter-exchange": "tasks.dlx",
    },
)
```

Важно: quorum-очереди **не поддерживают `x-max-priority`**. Приоритет
instant > digest решается разделением очередей (`send_instant`, `send_digest`),
а не приоритетом сообщений.

### 8.2. Контракты сообщений

```python
# app/transport/messaging/contracts.py
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class MessageEnvelope(BaseModel):
    """Общий конверт: версия схемы + ключ идемпотентности."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    idempotency_key: str = Field(min_length=8, max_length=128)


class FetchSourceCommand(MessageEnvelope):
    command: Literal["fetch_source_task"] = "fetch_source_task"
    source: str
    criteria: dict | None = None
    max_pages: int = 3
```

### 8.3. Хендлер

```python
# app/transport/messaging/handlers/fetch.py
import logging

from dishka_faststream import FromDishka
from faststream.rabbit import RabbitBroker

from app.application.listings.use_cases import FetchSource, FetchSourceInput
from app.application.shared.ports import IdempotencyGuard
from app.infrastructure.messaging.topology import FETCH_QUEUE, TASKS_EXCHANGE
from app.transport.messaging.contracts import FetchSourceCommand

logger = logging.getLogger(__name__)


def register(broker: RabbitBroker) -> None:
    @broker.subscriber(queue=FETCH_QUEUE, exchange=TASKS_EXCHANGE)
    async def handle_fetch(
        cmd: FetchSourceCommand,
        use_case: FromDishka[FetchSource],
        guard: FromDishka[IdempotencyGuard],
    ) -> None:
        """Обработать задачу парсинга источника.

        Идемпотентность по `idempotency_key`: повторная доставка того же
        сообщения не запускает парсинг и запись второй раз.
        """
        key = f"idem:fetch:{cmd.idempotency_key}"
        if not await guard.acquire(key, ttl_sec=7 * 24 * 3600):
            logger.info("fetch_duplicate_skipped key=%s", cmd.idempotency_key)
            return
        try:
            # Контракт сообщения живёт в transport, вход use case — в application.
            await use_case.execute(
                FetchSourceInput(
                    source=cmd.source,
                    criteria=cmd.criteria,
                    max_pages=cmd.max_pages,
                )
            )
        except Exception:
            # Пока транзакция не закоммичена — побочных эффектов нет,
            # ключ можно снять и дать сообщению переотправиться.
            await guard.release(key)
            raise
```

### 8.4. Запуск воркера и graceful shutdown

```python
# app/main_worker.py
import asyncio
import logging

from dishka_faststream import setup_dishka
from faststream.rabbit import RabbitBroker

from app.di.container import make_worker_container
from app.infrastructure.messaging import handlers
from app.infrastructure.messaging.relay import OutboxRelay

logger = logging.getLogger(__name__)


async def main() -> None:
    container = make_worker_container()
    broker = await container.get(RabbitBroker)

    for register in (handlers.fetch.register, handlers.send.register):
        register(broker)

    setup_dishka(container=container, broker=broker, auto_inject=True)

    # relay — APP-скоупные зависимости: фабрика сессий и «сырой» публикатор.
    session_factory = await container.get(async_sessionmaker[AsyncSession])
    publisher = await container.get(RawEventPublisher)
    relay = OutboxRelay(session_factory, publisher)
    stop = asyncio.Event()

    await broker.start()
    relay_task = asyncio.create_task(relay.run(stop))

    try:
        await stop.wait()
    finally:
        relay_task.cancel()
        # graceful_timeout=30 даёт дождаться in-flight сообщений
        await broker.stop()
        await container.close()


if __name__ == "__main__":
    logging.basicConfig(level="INFO")
    asyncio.run(main())
```

### 8.5. Ретраи и DLQ

| Механизм | Настройка | Что даёт |
|---|---|---|
| Повторная доставка | `x-delivery-limit: 5` на quorum-очереди | RabbitMQ сам считает доставки и после лимита отправляет в DLX |
| Backoff | TTL-очереди `fetch.retry.1/2/3` (опционально) | задержка между попытками вместо немедленного requeue |
| Permanent error | `reject(requeue=False)` в хендлере | не тратить 5 попыток на `ValidationError` |
| DLQ replay | CLI `python -m app.tools.replay_dlq --limit 100` | разбор `dead_letter` с идемпотентностью |

Ручной `while attempt < MAX_ATTEMPTS` внутри обработчика (как сейчас в
`TaskWorker`) убирается: повторная доставка — ответственность брокера,
обработчик должен быть идемпотентным.

### 8.6. Cron

Cron не должен жить в консьюмере. Отдельный процесс `worker_cron` публикует
команды с ключом слота:

```python
slot = f"{spec.name}:{target:%Y%m%d%H%M}"
await broker.publish(
    CronTick(job=spec.name, slot=slot, idempotency_key=slot),
    exchange=TASKS_EXCHANGE,
    routing_key="cron_tick",
)
```

Идемпотентность по `slot` заменяет Redis-локи: повторный запуск после
рестарта Redis не выполнит job второй раз.

---

## 9. Надёжность: что гарантирует схема

| Сценарий | Collect/flush | Outbox |
|---|---|---|
| Откат транзакции | события отбрасываются | строки outbox откатываются |
| Падение процесса после `commit`, до `publish` | событие потеряно | релей опубликует после рестарта |
| Падение после `publish`, до `commit` релея | — | повторная публикация (at-least-once) |
| Дубли в брокере | возможны | возможны → нужна идемпотентность консьюмера |
| Порядок событий | не гарантирован | по `id` outbox в пределах партиции/очереди |

Итог: **exactly-once в распределённой системе не бывает**. Схема даёт
at-least-once + идемпотентность, а не «магию».

---

## 10. Принятые решения (ADR-коротко)

| Решение | Альтернатива | Почему так |
|---|---|---|
| Оставить пакет `app/`, не переезжать в `src/` | `src/` как в промпте | 111 файлов импортов без выгоды; структура важнее имени папки |
| Домен — `dataclass(frozen=True, slots=True)` | Pydantic-модели в домене | домен не должен зависеть от библиотеки валидации; Pydantic — на границе |
| Dishka, а не только `Depends` | FastAPI `Depends` | один контейнер на HTTP + aiogram + FastStream; скоупы и подмена в тестах |
| FastStream, а не доработка `TaskWorker` | свой worker | типизация, DLQ, graceful shutdown, тестовый брокер — бесплатно |
| RabbitMQ остаётся | NATS/Kafka | топология и DLQ уже работают; смена брокера — отдельный проект |
| Outbox для денег | collect/flush везде | потеря `charge_lead` дороже, чем таблица + релей |
| Quorum-очереди | classic + `x-max-priority` | надёжность и delivery-limit важнее приоритетов; приоритет — отдельными очередями |
| Keyset-пагинация в API v2 | сразу убрать `offset` | контракт Mini App нельзя ломать; курсор вводим рядом |

---

## 11. Что сознательно не входит

- Разделение монолита на микросервисы «по-настоящему» — шесть сервисов уже
  есть, но их границы не меняем.
- CQRS/Event Sourcing — избыточно для 22 тыс. строк.
- Переписывание адаптеров источников — они изолированы лучше остального.
- Замена Postgres/Redis/Qdrant/MinIO.
