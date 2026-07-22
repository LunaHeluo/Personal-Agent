from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from starter_agent.capabilities.confirmations import (
    ConfirmationBroker,
    ConfirmationWaitTimeout,
)
from starter_agent.capabilities.models import Confirmation


def _confirmation(*, status="pending", decision=None) -> Confirmation:
    now = datetime.now(UTC)
    values = {
        "id": "confirmation-1",
        "principal": "local-user",
        "session_id": "session-1",
        "turn_id": "turn-1",
        "call_id": "call-1",
        "request_hash": "a" * 64,
        "server_id": "playwright",
        "tool_name": "browser_navigate",
        "schema_hash": "b" * 64,
        "arguments_hash": "c" * 64,
        "arguments_summary": {"url": "https://jobs.example.test/opening"},
        "risk": "external",
        "destination": "jobs.example.test",
        "expires_at": now + timedelta(minutes=1),
        "status": status,
        "decision": decision,
    }
    if status != "pending":
        values.update(
            decided_at=now,
            idempotency_key_hash="d" * 64,
        )
    return Confirmation(**values)


async def test_broker_resolves_waiter_and_resolution_before_wait_is_not_lost() -> None:
    broker = ConfirmationBroker()
    approved = _confirmation(status="approved", decision="once")
    waiter = asyncio.create_task(broker.wait(approved.id, timeout=1))
    await asyncio.sleep(0)

    assert broker.resolve(approved) is True
    assert await waiter == approved
    assert broker.resolve(approved) is False

    early = approved.model_copy(update={"id": "confirmation-early"})
    assert broker.resolve(early) is True
    assert await broker.wait(early.id, timeout=1) == early


async def test_broker_timeout_removes_waiter_without_resolving_tool_work() -> None:
    broker = ConfirmationBroker()

    with pytest.raises(ConfirmationWaitTimeout):
        await broker.wait("confirmation-timeout", timeout=0.01)

    assert broker.has_waiter("confirmation-timeout") is False
