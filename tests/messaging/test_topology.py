"""Топология RabbitMQ: имена, типы exchange и аргументы очередей."""

from __future__ import annotations

from faststream.rabbit import ExchangeType

from app.infrastructure.messaging import topology


def test_tasks_exchange_is_direct_and_durable() -> None:
    assert topology.TASKS_EXCHANGE.name == "tasks"
    assert topology.TASKS_EXCHANGE.type is ExchangeType.DIRECT
    assert topology.TASKS_EXCHANGE.durable is True


def test_dlx_exchange_is_fanout_and_durable() -> None:
    assert topology.DLX_EXCHANGE.name == "tasks.dlx"
    assert topology.DLX_EXCHANGE.type is ExchangeType.FANOUT
    assert topology.DLX_EXCHANGE.durable is True


def test_dead_letter_queue_is_durable() -> None:
    assert topology.DEAD_LETTER_QUEUE.name == "dead_letter"
    assert topology.DEAD_LETTER_QUEUE.durable is True


def test_work_queues_are_quorum_with_delivery_limit_and_dlx() -> None:
    expected = {
        topology.FETCH_QUEUE: "fetch_source_task",
        topology.SEND_QUEUE: "send_listing_notification",
        topology.ENRICH_QUEUE: "enrich_listing",
    }
    for queue, routing_key in expected.items():
        assert queue.routing_key == routing_key
        assert queue.durable is True
        assert queue.arguments["x-queue-type"] == "quorum"
        assert queue.arguments["x-delivery-limit"] == 5
        assert queue.arguments["x-dead-letter-exchange"] == "tasks.dlx"


def test_queue_names_match_legacy_worker() -> None:
    assert topology.FETCH_QUEUE.name == "fetch"
    assert topology.SEND_QUEUE.name == "send"
    assert topology.ENRICH_QUEUE.name == "enrich"


def test_dead_letter_queue_is_not_bound_to_dlx() -> None:
    """У DLQ нет DLX — иначе падение DLQ зациклится (routing — имя очереди)."""
    assert topology.DEAD_LETTER_QUEUE.routing() == "dead_letter"
    assert "x-dead-letter-exchange" not in topology.DEAD_LETTER_QUEUE.arguments
