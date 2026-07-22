from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from importlib import import_module

import pytest

from starter_agent.capabilities.models import ExecutionPermit, canonical_json_sha256
from starter_agent.capabilities.store import CapabilityStore


def _gate_module():
    try:
        return import_module("starter_agent.capabilities.gate")
    except ModuleNotFoundError:
        assert False, "Task 7 unified executor module is missing"


def _request(gate_module, *, server_id: str = "builtin", call_id: str = "call-1"):
    schema_hash = "a" * 64
    return gate_module.ToolCallRequest(
        caller="model",
        session_id="session-1",
        turn_id="turn-1",
        call_id=call_id,
        server_id=server_id,
        tool_name="safe_tool",
        snapshot_id="builtin-v1" if server_id == "builtin" else "snapshot-1",
        schema_hash=schema_hash,
        arguments={"value": 1},
    )


def _permit(request, *, permit_id: str, expires_at: datetime) -> ExecutionPermit:
    return ExecutionPermit(
        id=permit_id,
        request_hash=request.request_hash,
        policy_revision=0,
        expires_at=expires_at,
        caller=request.caller,
        session_id=request.session_id,
        turn_id=request.turn_id,
        server_id=request.server_id,
        tool_name=request.tool_name,
        snapshot_id=request.snapshot_id,
        schema_hash=request.schema_hash,
        arguments_hash=canonical_json_sha256(request.arguments),
        decision="allow",
    )


@pytest.mark.parametrize("server_id", ["builtin", "playwright"])
async def test_builtin_and_mcp_paths_refuse_calls_without_permit(server_id: str) -> None:
    gate_module = _gate_module()
    store = CapabilityStore("sqlite:///:memory:", project_root=__file__)
    executor = gate_module.UnifiedToolExecutor(store)

    with pytest.raises(gate_module.ToolExecutionDenied, match="permit_required"):
        await executor.execute(
            _request(gate_module, server_id=server_id),
            permit_id=None,
            invoker=lambda _arguments: "bypassed",
            forced=True,
            retry=True,
        )


async def test_permit_is_ttl_bound_single_use_and_atomically_consumed() -> None:
    gate_module = _gate_module()
    store = CapabilityStore("sqlite:///:memory:", project_root=__file__)
    executor = gate_module.UnifiedToolExecutor(store)
    request = _request(gate_module)
    permit = _permit(
        request,
        permit_id="permit-1",
        expires_at=datetime.now(UTC) + timedelta(seconds=30),
    )
    store.create_execution_permit(permit)

    entered = 0

    async def invoke(arguments):
        nonlocal entered
        entered += 1
        await asyncio.sleep(0)
        return arguments["value"]

    results = await asyncio.gather(
        executor.execute(request, permit_id=permit.id, invoker=invoke),
        executor.execute(request, permit_id=permit.id, invoker=invoke),
        return_exceptions=True,
    )
    assert results.count(1) == 1
    assert sum(isinstance(item, gate_module.ToolExecutionDenied) for item in results) == 1
    assert entered == 1

    expired = _permit(
        request,
        permit_id="expired",
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    store.create_execution_permit(expired)
    with pytest.raises(gate_module.ToolExecutionDenied, match="permit_expired"):
        await executor.execute(request, permit_id=expired.id, invoker=invoke)

    rebound = _permit(
        request,
        permit_id="bound",
        expires_at=datetime.now(UTC) + timedelta(seconds=30),
    )
    store.create_execution_permit(rebound)
    changed = request.model_copy(update={"turn_id": "turn-2"})
    with pytest.raises(gate_module.ToolExecutionDenied, match="permit_binding_mismatch"):
        await executor.execute(changed, permit_id=rebound.id, invoker=invoke)
