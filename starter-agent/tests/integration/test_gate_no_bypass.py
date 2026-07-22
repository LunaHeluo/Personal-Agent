from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from importlib import import_module

import pytest

from starter_agent.capabilities.models import ExecutionPermit, canonical_json_sha256
from starter_agent.capabilities.store import CapabilityStore
from starter_agent.bootstrap import create_application
from starter_agent.domain.models import ToolResult


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
        invoker_id=f"{request.server_id}:{request.tool_name}",
    )


@pytest.mark.parametrize("server_id", ["builtin", "playwright"])
async def test_builtin_and_mcp_paths_refuse_calls_without_permit(server_id: str) -> None:
    gate_module = _gate_module()
    store = CapabilityStore("sqlite:///:memory:", project_root=__file__)
    gate = gate_module.PreToolCallGate(store)
    executor = gate_module.UnifiedToolExecutor(store, gate=gate)

    with pytest.raises(gate_module.ToolExecutionDenied, match="permit_required"):
        await executor.execute(
            _request(gate_module, server_id=server_id),
            permit_id=None,
            forced=True,
            retry=True,
        )


async def test_permit_is_ttl_bound_single_use_and_atomically_consumed() -> None:
    gate_module = _gate_module()
    from tests.unit.test_pre_tool_call_gate import _public_resolver, _request as gate_request, _store
    policy_module = import_module("starter_agent.capabilities.policy")
    store = _store()
    gate = gate_module.PreToolCallGate(
        store,
        browser_policy=policy_module.BrowserScopePolicy(resolver=_public_resolver),
    )
    executor = gate_module.UnifiedToolExecutor(store, gate=gate)
    request = gate_request(gate_module)
    permit = (await gate.evaluate(request)).permit
    assert permit is not None

    entered = 0

    async def invoke(arguments, _context):
        nonlocal entered
        entered += 1
        await asyncio.sleep(0)
        return 1

    async def guarded(_request):
        return gate_module.NetworkGuardAttestation(
            targets=("https://jobs.example.com/opening",),
            dns_pinned=True,
            redirects_enforced=True,
            peer_verified=True,
        )

    executor.register_invoker(
        server_id=request.server_id,
        tool_name=request.tool_name,
        invoker=invoke,
        network_guard=guarded,
    )

    results = await asyncio.gather(
        executor.execute(request, permit_id=permit.id),
        executor.execute(request, permit_id=permit.id),
        return_exceptions=True,
    )
    assert results.count(1) == 1
    assert sum(isinstance(item, gate_module.ToolExecutionDenied) for item in results) == 1
    assert entered == 1

    expired = permit.model_copy(
        update={
            "id": "expired",
            "expires_at": datetime.now(UTC) - timedelta(seconds=1),
            "consumed_at": None,
        }
    )
    store.create_execution_permit(expired)
    with pytest.raises(gate_module.ToolExecutionDenied, match="permit_expired"):
        await executor.execute(request, permit_id=expired.id)

    rebound = permit.model_copy(
        update={
            "id": "bound",
            "expires_at": datetime.now(UTC) + timedelta(seconds=30),
            "consumed_at": None,
        }
    )
    store.create_execution_permit(rebound)
    changed = request.model_copy(update={"turn_id": "turn-2"})
    with pytest.raises(gate_module.ToolExecutionDenied, match="permit_binding_mismatch"):
        await executor.execute(changed, permit_id=rebound.id)


async def test_real_agent_runtime_forced_builtin_uses_gate_and_bound_executor(
    monkeypatch,
) -> None:
    create_application.cache_clear()
    application = create_application()
    runtime = application.runtime
    assert runtime.gate is not None
    assert runtime.executor is not None
    tool = runtime.tools.get("get_current_time")
    assert tool is not None
    executed = 0

    async def execute(arguments, context):
        nonlocal executed
        executed += 1
        return ToolResult(ok=True, data={"time": "00:00"}, display="00:00")

    monkeypatch.setattr(tool, "execute", execute)
    result = await application.chat(
        "run the time tool",
        provider_name="mock",
        required_tool_name="get_current_time",
    )

    assert result.tool_calls == 1
    assert executed == 1


def test_bootstrap_seeds_explicit_safe_builtin_allowlist_only() -> None:
    create_application.cache_clear()
    runtime = create_application().runtime
    time_rules = runtime.gate.store.list_policy_rules("builtin", "get_current_time")
    resume_rules = runtime.gate.store.list_policy_rules("builtin", "read_resume")
    email_rules = runtime.gate.store.list_policy_rules("builtin", "email_read")

    assert any(rule.effect == "allowlist_auto" for rule in time_rules)
    assert not any(rule.effect == "allowlist_auto" for rule in resume_rules)
    assert not any(rule.effect == "allowlist_auto" for rule in email_rules)


async def test_executor_revalidates_and_cannot_accept_arbitrary_invoker() -> None:
    gate_module = _gate_module()
    from tests.unit.test_pre_tool_call_gate import _public_resolver, _request, _store

    policy_module = import_module("starter_agent.capabilities.policy")
    store = _store()
    gate = gate_module.PreToolCallGate(
        store,
        browser_policy=policy_module.BrowserScopePolicy(resolver=_public_resolver),
    )
    executor = gate_module.UnifiedToolExecutor(store, gate=gate)
    request = _request(gate_module)
    decision = await gate.evaluate(request)
    assert decision.permit is not None

    with pytest.raises(TypeError):
        await executor.execute(
            request,
            permit_id=decision.permit.id,
            invoker=lambda _arguments: "bypass",
        )

    with pytest.raises(TypeError):
        await executor.execute(
            request,
            permit_id=decision.permit.id,
            context=object(),
        )

    executor.register_invoker(
        server_id="playwright",
        tool_name="different_tool",
        invoker=lambda _arguments, _context: "wrong",
    )
    with pytest.raises(gate_module.ToolExecutionDenied, match="invoker_unavailable"):
        await executor.execute(request, permit_id=decision.permit.id)


def test_browser_invoker_registration_requires_network_guard_attestation() -> None:
    gate_module = _gate_module()
    from tests.unit.test_pre_tool_call_gate import _public_resolver, _store
    from starter_agent.capabilities.registry import UnifiedToolRegistry
    from starter_agent.tools.registry import ToolRegistry

    store = _store()
    snapshot = store.get_active_snapshot("playwright")
    assert snapshot is not None
    registry = UnifiedToolRegistry(ToolRegistry([]))
    registry.refresh_server(
        store.get_server("playwright"),
        store.list_tools(snapshot.id),
        snapshot=snapshot,
    )
    gate = gate_module.PreToolCallGate(
        store,
        registry=registry,
        browser_policy=import_module("starter_agent.capabilities.policy").BrowserScopePolicy(
            resolver=_public_resolver
        ),
    )
    executor = gate_module.UnifiedToolExecutor(store, gate=gate)

    with pytest.raises(
        gate_module.ToolExecutionDenied,
        match="network_guard_required",
    ):
        executor.register_invoker(
            server_id="playwright",
            tool_name="browser_navigate",
            invoker=lambda _arguments, _context: None,
        )


async def test_mcp_manager_business_call_requires_executor_permit_and_lease_is_opaque(
    tmp_path,
) -> None:
    gate_module = _gate_module()
    from starter_agent.mcp.config import McpConfiguration
    from starter_agent.mcp.manager import ClientLease, McpManager, McpManagerError

    manager = McpManager(
        McpConfiguration(
            source_path=tmp_path / "mcp.json",
            servers={},
            config_hash="a" * 64,
        ),
        store=CapabilityStore("sqlite:///:memory:", tmp_path),
    )
    with pytest.raises(McpManagerError, match="permit_required"):
        await manager.call_tool(
            _request(gate_module, server_id="playwright"),
            permit_id=None,
        )
    assert "client" not in ClientLease.__dataclass_fields__
    assert "session" not in ClientLease.__dataclass_fields__
    assert "_client" not in ClientLease.__dataclass_fields__
    assert "_session" not in ClientLease.__dataclass_fields__
    assert not hasattr(manager, "get_handle")
