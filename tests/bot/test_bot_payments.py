"""TECHDEBT 6.9/2.9/2.10: платежи бота — payload, ретраи, антидаблклик."""

from types import SimpleNamespace

import pytest

from app.transport.bot import handlers


def _cb(data: str, user_id: int = 5) -> SimpleNamespace:
    answered = SimpleNamespace(calls=[])

    async def answer(*a, **kw):
        answered.calls.append((a, kw))

    async def answer_invoice(**kw):
        answered.calls.append((("invoice",), kw))

    return SimpleNamespace(
        data=data,
        from_user=SimpleNamespace(id=user_id),
        message=SimpleNamespace(answer_invoice=answer_invoice),
        answer=answer,
        _answered=answered,
    )


@pytest.fixture(autouse=True)
def _reset_cooldown():
    handlers._SUB_INVOICE_AT.clear()
    yield
    handlers._SUB_INVOICE_AT.clear()


@pytest.mark.asyncio
async def test_pre_checkout_rejects_bad_payload():
    """6.9: payload не вида `sub:<id>` → ok=False, Stars не списываются."""
    answers = []

    async def answer(**kw):
        answers.append(kw)

    query = SimpleNamespace(invoice_payload="free:stars:999", answer=answer)
    await handlers.pre_checkout_query(query)
    assert answers and answers[0]["ok"] is False

    query2 = SimpleNamespace(invoice_payload="sub:abc", answer=answer)
    await handlers.pre_checkout_query(query2)
    assert answers[-1]["ok"] is False


@pytest.mark.asyncio
async def test_pre_checkout_accepts_valid_payload():
    answers = []

    async def answer(**kw):
        answers.append(kw)

    query = SimpleNamespace(invoice_payload="sub:123", answer=answer)
    await handlers.pre_checkout_query(query)
    assert answers == [{"ok": True}]


@pytest.mark.asyncio
async def test_sub_buy_double_click_single_invoice(monkeypatch):
    """2.10: два быстрых клика → один prepare_payment (второй отброшен)."""
    calls = []

    async def prepare(user_id, tier, days):
        calls.append((user_id, tier, days))
        return {
            "title": "Pro",
            "description": "Подписка Pro на 30 дней",
            "payment_id": 42,
            "amount": 500,
        }

    monkeypatch.setattr(handlers.backend, "prepare_payment", prepare)

    cb1 = _cb("sub:buy:pro:30")
    await handlers.sub_buy(cb1)
    cb2 = _cb("sub:buy:pro:30")
    await handlers.sub_buy(cb2)

    assert len(calls) == 1  # второй клик в кулдауне отброшен


@pytest.mark.asyncio
async def test_payment_success_retries_then_alerts(monkeypatch):
    """2.9: backend лежит → 3 попытки, затем alert + сообщение про менеджера."""
    attempts = []

    async def payment_success(**kw):
        attempts.append(kw)
        return None

    async def no_sleep(*a, **kw):
        return None

    alerts = []

    def fake_fire(coro):
        alerts.append(coro)
        coro.close()  # не запускаем реальный alert

    monkeypatch.setattr(handlers.backend, "payment_success", payment_success)
    monkeypatch.setattr(handlers, "asyncio", SimpleNamespace(sleep=no_sleep))
    monkeypatch.setattr(handlers, "fire", fake_fire)

    sent = []

    async def answer(text=None, **kw):
        sent.append(text)

    message = SimpleNamespace(
        answer=answer,
        from_user=SimpleNamespace(id=7),
        successful_payment=SimpleNamespace(
            invoice_payload="sub:55",
            telegram_payment_charge_id="chg1",
            total_amount=500,
        ),
    )
    await handlers.on_successful_payment(message)

    assert len(attempts) == 3  # ретраи выполнены
    assert len(alerts) == 1  # эскалация админу
    assert any("не удалась" in (t or "") for t in sent)


@pytest.mark.asyncio
async def test_payment_success_first_try_no_alert(monkeypatch):
    """2.9: первая попытка успешна — без ретраев и алертов."""
    attempts = []

    async def payment_success(**kw):
        attempts.append(kw)
        return {"tier": "pro", "subscription_until": "2030-01-01"}

    async def no_sleep(*a, **kw):
        return None

    monkeypatch.setattr(handlers.backend, "payment_success", payment_success)
    monkeypatch.setattr(handlers, "asyncio", SimpleNamespace(sleep=no_sleep))

    sent = []

    async def answer(text=None, **kw):
        sent.append(text)

    message = SimpleNamespace(
        answer=answer,
        from_user=SimpleNamespace(id=8),
        successful_payment=SimpleNamespace(
            invoice_payload="sub:56",
            telegram_payment_charge_id="chg2",
            total_amount=500,
        ),
    )
    await handlers.on_successful_payment(message)
    assert len(attempts) == 1
    assert any("активирован" in (t or "") for t in sent)