"""Deal-тред из заявки на агента (B2 §1): RequestAgent → лид+тред+system.

Юнит-тесты на фейках (без БД): заявка создаёт deal-тред с системным
сообщением, повтор (идемпотентность лида) переиспользует тот же тред,
claim лида партнёром привязывает тред к счёту и пишет «агент подключился».
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from app.application.errors import Forbidden
from app.application.leads.deals import AGENT_DEAL_CREATED, RequestAgent
from app.application.partners.dto import CreateLeadCommand, LeadView
from app.application.partners.use_cases import AGENT_CONNECTED, ClaimLead
from app.domain.partners.ports import (
    CLAIM_OK,
)
from app.domain.subscription.policies import TIERS
from tests.partners.test_partners import FakePartnerRepository

pytestmark = pytest.mark.asyncio

USER_ID = 900200
LISTING_ID = 555
PARTNER_ID = 7


class FakeUoW:
    """UoW-заглушка: фиксирует commit (как в тестах партнёров)."""

    def __init__(self) -> None:
        self.committed = False

    async def __aenter__(self) -> "FakeUoW":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        return None


# =========================================================================
# Фейки
# =========================================================================


class FakeUsers:
    """UserLookupPort с тарифом (business проходит гейт агента)."""

    def __init__(self, tier: str = "business") -> None:
        self.tier = tier

    async def get(self, user_id: int):
        from app.application.dialog.dto import UserProfile

        return UserProfile(user_id=user_id, is_active=True, tier=self.tier)


class FakeCreateLead:
    """CreateLead-двойник: идемпотентный по ключу, id инкрементируется."""

    def __init__(self) -> None:
        self.commands: list[CreateLeadCommand] = []
        self.by_key: dict[str, LeadView] = {}
        self._next_id = 1

    async def execute(self, command: CreateLeadCommand) -> LeadView:
        self.commands.append(command)
        if command.idempotency_key and command.idempotency_key in self.by_key:
            return self.by_key[command.idempotency_key]
        lead = LeadView(
            id=self._next_id,
            user_id=command.user_id,
            listing_id=command.listing_id,
            source=command.source,
            status="new",
            manager_id=None,
            comment=command.comment,
            created_at=datetime.now(UTC),
        )
        self._next_id += 1
        if command.idempotency_key:
            self.by_key[command.idempotency_key] = lead
        return lead


class FakeDealChatRepo:
    """ChatRepository для deal-тредов: reuse по (type, lead_id)."""

    def __init__(self) -> None:
        self.threads: dict[tuple[str, int], tuple[UUID, int | None]] = {}
        self.messages: dict[UUID, list[tuple[str, str]]] = defaultdict(list)
        self._by_lead: dict[int, UUID] = {}
        self.created_count = 0

    async def get_or_create_thread(
        self,
        *,
        type: str,  # noqa: A002
        user_id: int,
        partner_id: int | None = None,
        listing_id: int | None = None,
        lead_id: int | None = None,
    ):
        from app.application.chat.dto import ThreadRecord

        assert type == "deal" and lead_id is not None
        existing = self._by_lead.get(lead_id)
        if existing is not None:
            record = ThreadRecord(
                id=existing,
                type="deal",
                user_id=user_id,
                partner_id=self.threads[("deal", lead_id)][1],
                lead_id=lead_id,
            )
            return record, False
        thread_id = uuid4()
        self._by_lead[lead_id] = thread_id
        self.threads[("deal", lead_id)] = (thread_id, partner_id)
        self.created_count += 1
        record = ThreadRecord(
            id=thread_id,
            type="deal",
            user_id=user_id,
            partner_id=partner_id,
            lead_id=lead_id,
        )
        return record, True

    async def add_message(self, command) -> None:
        self.messages[command.thread_id].append(
            (command.sender.user_type, command.body)
        )

    async def attach_partner_to_deal(self, lead_id: int, partner_id: int):
        from app.application.chat.dto import ThreadRecord

        thread_id = self._by_lead.get(lead_id)
        if thread_id is None:
            return None
        self.threads[("deal", lead_id)] = (thread_id, partner_id)
        return ThreadRecord(
            id=thread_id,
            type="deal",
            user_id=USER_ID,
            partner_id=partner_id,
            lead_id=lead_id,
        )


def _agent(chats: FakeDealChatRepo | None = None):
    """Собрать RequestAgent на фейках (business-тариф по умолчанию)."""
    create_lead = FakeCreateLead()
    uow = FakeUoW()
    return (
        RequestAgent(
            FakeUsers(),  # type: ignore[arg-type]
            create_lead,  # type: ignore[arg-type]
            uow=uow,  # type: ignore[arg-type]
            chats=chats,  # type: ignore[arg-type]
        ),
        create_lead,
        uow,
    )


# =========================================================================
# POST /agent → лид + deal-тред
# =========================================================================


async def test_agent_creates_lead_and_deal_thread():
    """Заявка: лид source=agent, deal-тред и system-сообщение о заявке."""
    chats = FakeDealChatRepo()
    agent, create_lead, uow = _agent(chats)

    result = await agent.execute(USER_ID, LISTING_ID, comment="нужен осмотр")

    assert result.lead.id == 1
    assert result.lead.source == "agent"
    assert result.thread_created is True
    assert result.thread_id is not None
    # Ключ идемпотентности — agent:user:listing (ретраи не плодят лиды)
    assert create_lead.commands[0].idempotency_key == f"agent:{USER_ID}:{LISTING_ID}"
    # Тред привязан к лиду, партнёра пока нет
    thread, partner = chats.threads[("deal", 1)]
    assert thread == result.thread_id and partner is None
    # System-сообщение о заявке (§1, §5)
    (sender, body), = chats.messages[result.thread_id]
    assert sender == "system"
    assert body == AGENT_DEAL_CREATED.format(lead_id=1)
    assert uow.committed is True


async def test_agent_repeat_reuses_same_thread():
    """Повторная заявка: тот же лид (идемпотентность) → тот же тред, без дубля."""
    chats = FakeDealChatRepo()
    agent, create_lead, _ = _agent(chats)

    first = await agent.execute(USER_ID, LISTING_ID)
    second = await agent.execute(USER_ID, LISTING_ID)

    assert second.lead.id == first.lead.id
    assert second.thread_id == first.thread_id
    assert second.thread_created is False
    assert chats.created_count == 1
    # System-сообщение не дублируется
    assert len(chats.messages[first.thread_id]) == 1
    # CreateLead вызывался, но идемпотентный ключ вернул тот же лид
    assert len(create_lead.commands) == 2


async def test_agent_without_chats_still_creates_lead():
    """Чаты не собраны (chats=None) — заявка работает, треда просто нет."""
    agent, _, _ = _agent(None)
    result = await agent.execute(USER_ID, LISTING_ID)
    assert result.lead.id == 1
    assert result.thread_id is None and result.thread_created is False


async def test_agent_forbidden_for_non_business():
    """Не-Business тариф → 403 agent_forbidden, лида и треда нет."""
    chats = FakeDealChatRepo()
    create_lead = FakeCreateLead()
    agent = RequestAgent(
        FakeUsers(tier="free"),  # type: ignore[arg-type]
        create_lead,  # type: ignore[arg-type]
        uow=FakeUoW(),  # type: ignore[arg-type]
        chats=chats,  # type: ignore[arg-type]
    )
    assert not TIERS["free"].agent
    with pytest.raises(Forbidden) as exc:
        await agent.execute(USER_ID, LISTING_ID)
    assert "agent_forbidden" in exc.value.public_message
    assert create_lead.commands == [] and chats.created_count == 0


# =========================================================================
# Claim лида → привязка deal-треда
# =========================================================================


async def test_claim_links_deal_thread_and_announces_agent():
    """Успешный claim: тред привязан к счёту, system «Агент ... подключился»."""
    repo = FakePartnerRepository(balance=Decimal("100.00"), plan="pro")
    repo.free = {1}
    chats = FakeDealChatRepo()
    # Предусловие: заявку на агента уже создали (лид 1 + deal-тред)
    agent, _, _ = _agent(chats)
    created = await agent.execute(USER_ID, LISTING_ID)
    uow = FakeUoW()

    # FakePartnerRepository держит счёт с id=1
    result = await ClaimLead(
        uow, repo, chats=chats  # type: ignore[arg-type]
    ).execute(1, 1)

    assert result.reason == CLAIM_OK and result.lead_ids == (1,)
    thread_id, partner_id = chats.threads[("deal", 1)]
    assert thread_id == created.thread_id
    assert partner_id == 1
    messages = chats.messages[thread_id]
    assert (messages[-1][0], messages[-1][1]) == (
        "system",
        AGENT_CONNECTED.format(partner_name=repo.account.name),
    )
    assert uow.committed is True


async def test_claim_without_chat_repository_unchanged():
    """Без чатов (chats=None) claim работает как раньше."""
    repo = FakePartnerRepository(balance=Decimal("100.00"), plan="pro")
    repo.free = {7}
    result = await ClaimLead(FakeUoW(), repo).execute(1, 7)
    assert result.reason == CLAIM_OK and result.lead_ids == (7,)


async def test_claim_lead_without_deal_thread_is_noop_for_chats():
    """Лид из пула без deal-треда («свяжитесь со мной») — чаты не трогаются."""
    repo = FakePartnerRepository(balance=Decimal("100.00"), plan="pro")
    repo.free = {42}  # лида 42 нет ни в одном треде
    chats = FakeDealChatRepo()
    uow = FakeUoW()

    result = await ClaimLead(
        uow, repo, chats=chats  # type: ignore[arg-type]
    ).execute(1, 42)

    assert result.reason == CLAIM_OK
    assert chats.threads == {} and chats.messages == {}
