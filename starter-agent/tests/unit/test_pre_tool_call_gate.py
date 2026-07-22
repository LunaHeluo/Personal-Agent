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
