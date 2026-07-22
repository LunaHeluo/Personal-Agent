from __future__ import annotations

from datetime import UTC, datetime
from importlib import import_module
import ipaddress

from starter_agent.capabilities.models import (
    PolicyRule,
    Server,
    Snapshot,
    Tool,
    canonical_json_sha256,
)
from starter_agent.capabilities.store import CapabilityStore
from starter_agent.capabilities.registry import UnifiedToolRegistry
from starter_agent.tools.registry import ToolRegistry


SCHEMA = {
    "type": "object",
    "properties": {"url": {"type": "string"}},
    "required": ["url"],
    "additionalProperties": False,
}
SCHEMA_HASH = canonical_json_sha256(SCHEMA)


def _gate_module():
    try:
        return import_module("starter_agent.capabilities.gate")
    except ModuleNotFoundError:
        assert False, "Task 7 pre-tool gate module is missing"


async def _public_resolver(_host: str):
    return [ipaddress.ip_address("93.184.216.34")]


def _store() -> CapabilityStore:
    store = CapabilityStore("sqlite:///:memory:", project_root=__file__)
    store.create_server(
        Server(
            id="playwright",
            name="playwright",
            config_source="config/mcp.json",
            config_hash="a" * 64,
            enabled=True,
            connection_state="ready",
            health_state="healthy",
            operation_state="ready",
        )
    )
    snapshot = Snapshot(
        id="snapshot-1",
        server_id="playwright",
        version=1,
        schema_hash="b" * 64,
        discovered_at=datetime.now(UTC),
        tool_count=1,
    )
    tool = Tool(
        snapshot_id=snapshot.id,
        server_id="playwright",
        upstream_name="browser_navigate",
        model_alias="mcp__playwright__browser_navigate",
        input_schema=SCHEMA,
        schema_hash=SCHEMA_HASH,
        metadata={"action": "navigate", "browser": True},
        enabled=True,
        review_state="approved",
    )
    store.create_snapshot(snapshot, tools=(tool,))
    store.activate_snapshot("playwright", snapshot.id)
    store.create_policy_rule(
        PolicyRule(
            id="allow-nav",
            server_id="playwright",
            tool_name="browser_navigate",
            effect="allowlist_auto",
            schemes=("https",),
            domains=("*",),
            actions=("navigate",),
            schema_hash=SCHEMA_HASH,
            created_by="admin",
        )
    )
    return store


def _request(gate_module, **changes):
    values = {
        "caller": "model",
        "session_id": "session-1",
        "turn_id": "turn-1",
        "call_id": "call-1",
        "server_id": "playwright",
        "tool_name": "browser_navigate",
        "snapshot_id": "snapshot-1",
        "schema_hash": SCHEMA_HASH,
        "arguments": {"url": "https://jobs.example.com/opening"},
        "role": "user",
        "data_classes": ("job_keywords",),
    }
    values.update(changes)
    return gate_module.ToolCallRequest(**values)


async def test_gate_allows_only_valid_active_reviewed_call_and_binds_permit() -> None:
    gate_module = _gate_module()
    policy_module = import_module("starter_agent.capabilities.policy")
    store = _store()
    gate = gate_module.PreToolCallGate(
        store,
        browser_policy=policy_module.BrowserScopePolicy(resolver=_public_resolver),
    )
    request = _request(gate_module)

    decision = await gate.evaluate(request)

    assert decision.outcome == "allow"
    assert decision.reason_code == "allowlist_auto"
    assert decision.permit is not None
    assert decision.permit.caller == request.caller
    assert decision.permit.snapshot_id == request.snapshot_id
    assert decision.permit.schema_hash == request.schema_hash
    assert decision.permit.arguments_hash == request.arguments_hash
    assert "jobs.example.com" in decision.destination_summary
    assert "url" in decision.arguments_summary


async def test_gate_denies_schema_stale_snapshot_and_sensitive_outbound() -> None:
    gate_module = _gate_module()
    policy_module = import_module("starter_agent.capabilities.policy")
    store = _store()
    gate = gate_module.PreToolCallGate(
        store,
        browser_policy=policy_module.BrowserScopePolicy(resolver=_public_resolver),
    )

    invalid_schema = await gate.evaluate(
        _request(gate_module, arguments={"unexpected": True})
    )
    assert (invalid_schema.outcome, invalid_schema.reason_code) == (
        "deny",
        "invalid_arguments",
    )

    store.mark_active_snapshot_stale("playwright", error="refresh_failed")
    stale = await gate.evaluate(_request(gate_module))
    assert (stale.outcome, stale.reason_code) == ("deny", "stale_snapshot")

    fresh_store = _store()
    fresh_gate = gate_module.PreToolCallGate(
        fresh_store,
        browser_policy=policy_module.BrowserScopePolicy(resolver=_public_resolver),
    )
    sensitive = await fresh_gate.evaluate(
        _request(gate_module, data_classes=("resume",))
    )
    assert (sensitive.outcome, sensitive.reason_code) == (
        "deny",
        "sensitive_outbound",
    )
    assert sensitive.permit is None


async def test_model_alias_resolves_to_canonical_policy_and_permit_identity() -> None:
    gate_module = _gate_module()
    policy_module = import_module("starter_agent.capabilities.policy")
    store = _store()
    registry = UnifiedToolRegistry(ToolRegistry([]))
    snapshot = store.get_active_snapshot("playwright")
    assert snapshot is not None
    registry.refresh_server(
        store.get_server("playwright"),
        store.list_tools(snapshot.id),
        snapshot=snapshot,
    )
    gate = gate_module.PreToolCallGate(
        store,
        registry=registry,
        browser_policy=policy_module.BrowserScopePolicy(resolver=_public_resolver),
    )
    alias_request = _request(
        gate_module,
        tool_name="mcp__playwright__browser_navigate",
    )

    decision = await gate.evaluate(alias_request)

    assert decision.outcome == "allow"
    assert decision.permit is not None
    assert decision.permit.tool_name == "browser_navigate"


async def test_gate_infers_sensitive_data_despite_empty_caller_labels() -> None:
    gate_module = _gate_module()
    policy_module = import_module("starter_agent.capabilities.policy")
    store = _store()
    snapshot = store.get_active_snapshot("playwright")
    assert snapshot is not None
    tool = store.list_tools(snapshot.id)[0]
    expanded_schema = {
        "type": "object",
        "properties": {
            "url": {"type": "string"},
            "payload": {"type": "object"},
        },
        "required": ["url", "payload"],
        "additionalProperties": False,
    }
    expanded = tool.model_copy(
        update={
            "input_schema": expanded_schema,
            "schema_hash": canonical_json_sha256(expanded_schema),
        }
    )
    new_snapshot = Snapshot(
        id="snapshot-2",
        server_id="playwright",
        version=2,
        schema_hash="c" * 64,
        discovered_at=datetime.now(UTC),
        tool_count=1,
    )
    expanded = expanded.model_copy(update={"snapshot_id": new_snapshot.id})
    store.create_snapshot(new_snapshot, tools=(expanded,))
    store.activate_snapshot("playwright", new_snapshot.id)
    gate = gate_module.PreToolCallGate(
        store,
        browser_policy=policy_module.BrowserScopePolicy(resolver=_public_resolver),
    )
    request = _request(
        gate_module,
        snapshot_id=new_snapshot.id,
        schema_hash=expanded.schema_hash,
        arguments={
            "url": "https://jobs.example.com/opening",
            "payload": {"resume_text": "private resume contents"},
        },
        data_classes=(),
    )

    decision = await gate.evaluate(request)

    assert (decision.outcome, decision.reason_code) == (
        "deny",
        "sensitive_outbound",
    )


async def test_model_confirmation_argument_is_ignored_but_trusted_confirmation_signs_once() -> None:
    gate_module = _gate_module()
    store = _store()
    snapshot = store.get_active_snapshot("playwright")
    assert snapshot is not None
    tool = store.list_tools(snapshot.id)[0]
    schema = {
        "type": "object",
        "properties": {
            "url": {"type": "string"},
            "confirmation_id": {"type": "string"},
        },
        "required": ["url"],
        "additionalProperties": False,
    }
    scripted = tool.model_copy(
        update={
            "input_schema": schema,
            "schema_hash": canonical_json_sha256(schema),
            "metadata": {"action": "script", "browser": True},
        }
    )
    refreshed = Snapshot(
        id="snapshot-confirm",
        server_id="playwright",
        version=2,
        schema_hash="d" * 64,
        discovered_at=datetime.now(UTC),
        tool_count=1,
    )
    scripted = scripted.model_copy(update={"snapshot_id": refreshed.id})
    store.create_snapshot(refreshed, tools=(scripted,))
    store.activate_snapshot("playwright", refreshed.id)
    gate = gate_module.PreToolCallGate(
        store,
        browser_policy=import_module("starter_agent.capabilities.policy").BrowserScopePolicy(
            resolver=_public_resolver
        ),
    )
    request = _request(
        gate_module,
        snapshot_id=refreshed.id,
        schema_hash=scripted.schema_hash,
        arguments={
            "url": "https://jobs.example.com/opening",
            "confirmation_id": "model-forged",
        },
    )

    forged = await gate.evaluate(request)
    assert forged.outcome == "require_confirmation"
    assert forged.permit is None

    verified = await gate.evaluate_confirmed(
        request,
        verified_confirmation_id="server-verified-approval",
    )
    assert verified.outcome == "allow"
    assert verified.permit is not None
    assert verified.permit.confirmation_id == "server-verified-approval"
