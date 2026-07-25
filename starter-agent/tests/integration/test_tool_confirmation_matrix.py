from __future__ import annotations

import asyncio
from typing import Any

import pytest

from starter_agent.capabilities.confirmations import (
    ConfirmationService,
    TurnCoordinator,
)
from starter_agent.capabilities.gate import (
    NetworkGuardAttestation,
    UnifiedToolExecutor,
)
from starter_agent.capabilities.registry import UnifiedToolRegistry
from starter_agent.tools.registry import ToolRegistry
from tests.integration.test_confirmation_execution_barrier import (
    _runtime,
    test_cancel_and_timeout_never_enter_real_tool as _cancel_timeout_case,
    test_runtime_waits_without_calling_tool_then_executes_once_after_approval as _once_case,
    test_two_permits_racing_confirmation_cas_enter_invoker_once as _concurrent_case,
)
from tests.unit.test_tool_confirmations import (
    _confirmation_gate,
    _request,
    test_always_confirm_rejects_allowlist_without_creating_rule as _always_case,
)


@pytest.mark.asyncio
async def test_unconfirmed_call_creates_no_permit_or_invocation(tmp_path) -> None:
    runtime, service, tool = _runtime(tmp_path)
    request = runtime.gate.request_for_tool(
        caller="model",
        session_id="auto-session",
        turn_id="auto-turn",
        call_id="auto-call",
        tool_name=tool.name,
        arguments={"value": 1},
    )
    decision = await runtime.gate.evaluate(request)

    assert decision.outcome == "require_confirmation"
    assert decision.permit is None
    assert tool.calls == 0
    assert service.list_pending(session_id=None) == []


@pytest.mark.asyncio
async def test_once_approval_and_consumed_replay_execute_exactly_once(tmp_path) -> None:
    await _once_case(tmp_path)


@pytest.mark.asyncio
async def test_allowlist_approval_makes_next_matching_read_auto_executable(
    tmp_path,
) -> None:
    store, gate = _confirmation_gate(tmp_path)
    snapshot = store.get_active_snapshot("playwright")
    server = store.get_server("playwright")
    assert snapshot is not None and server is not None
    registry = UnifiedToolRegistry(ToolRegistry([]))
    registry.refresh_server(
        server,
        store.list_tools(snapshot.id),
        snapshot=snapshot,
    )
    gate.registry = registry
    request = _request()
    service = ConfirmationService(store, gate)
    coordinator = TurnCoordinator(service, confirmation_timeout_seconds=1)
    initial = await gate.evaluate(request)
    wait = asyncio.create_task(coordinator.wait_for_permit(request, initial))
    for _ in range(100):
        pending_values = service.list_pending(session_id=request.session_id)
        if pending_values:
            break
        await asyncio.sleep(0.01)
    else:
        raise AssertionError("allowlist confirmation was not persisted")
    pending = pending_values[-1]
    approved = service.decide(
        pending.id,
        expected_revision=pending.revision,
        idempotency_key="allowlist-decision",
        decision="allowlist",
    )
    assert approved.status == "approved"
    first_permit = await wait
    assert first_permit.outcome == "allow"

    invocations: list[dict[str, Any]] = []
    executor = UnifiedToolExecutor(store, gate=gate)

    async def invoke(arguments, _context):
        invocations.append(dict(arguments))
        return {"ok": True}

    executor.register_invoker(
        server_id=request.server_id,
        tool_name=request.tool_name,
        invoker=invoke,
        network_guard=lambda current: NetworkGuardAttestation(
            targets=(current.arguments["url"],),
            dns_pinned=True,
            redirects_enforced=True,
            peer_verified=True,
        ),
    )
    next_request = request.model_copy(
        update={"turn_id": "turn-2", "call_id": "call-2"}
    )
    auto = await gate.evaluate(next_request)

    assert (auto.outcome, auto.reason_code) == ("allow", "allowlist_auto")
    assert invocations == []
    assert auto.permit is not None
    await executor.execute(next_request, permit_id=auto.permit.id)
    assert invocations == [dict(next_request.arguments)]


@pytest.mark.asyncio
async def test_always_confirm_cannot_be_downgraded_to_allowlist(tmp_path) -> None:
    await _always_case(tmp_path)


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", ["cancel", None], ids=["cancel", "timeout"])
async def test_cancel_and_timeout_keep_invoker_at_zero(tmp_path, terminal) -> None:
    await _cancel_timeout_case(tmp_path, terminal)


@pytest.mark.asyncio
async def test_confirmation_decision_replay_is_idempotent_without_invocation(
    tmp_path,
) -> None:
    store, gate = _confirmation_gate(tmp_path)
    service = ConfirmationService(store, gate)
    request = _request()
    pending = service.create_pending(request, await gate.evaluate(request))

    approved = service.decide(
        pending.id,
        expected_revision=pending.revision,
        idempotency_key="replay-decision",
        decision="once",
    )
    replay = service.decide(
        pending.id,
        expected_revision=pending.revision,
        idempotency_key="replay-decision",
        decision="once",
    )

    assert replay == approved
    assert approved.status == "approved"
    assert not any(
        event.action == "tool.invoked" for event in store.list_audit_events()
    )


@pytest.mark.asyncio
async def test_concurrent_confirmation_consumption_enters_invoker_once(
    tmp_path,
    monkeypatch,
) -> None:
    await _concurrent_case(tmp_path, monkeypatch)
