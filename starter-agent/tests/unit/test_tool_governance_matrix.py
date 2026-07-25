from __future__ import annotations

import ipaddress
from datetime import UTC, datetime
from typing import Any

import pytest

from starter_agent.capabilities.gate import (
    NetworkGuardAttestation,
    PreToolCallGate,
    ToolCallRequest,
    UnifiedToolExecutor,
)
from starter_agent.capabilities.models import (
    PolicyRule,
    Server,
    Snapshot,
    Tool,
    canonical_json_sha256,
)
from starter_agent.capabilities.policy import BrowserScopePolicy
from starter_agent.capabilities.store import CapabilityStore


SCHEMA = {
    "type": "object",
    "properties": {
        "url": {"type": "string"},
        "timeout": {"type": "integer", "minimum": 1, "maximum": 30},
    },
    "required": ["url"],
    "additionalProperties": False,
}
SCHEMA_HASH = canonical_json_sha256(SCHEMA)


async def _public_resolver(_host: str):
    return [ipaddress.ip_address("93.184.216.34")]


def _boundary(case: dict[str, Any]):
    store = CapabilityStore("sqlite:///:memory:", __file__)
    server = Server(
        id="browser",
        name="browser",
        config_source="tests/mcp.json",
        config_hash="a" * 64,
        enabled=case.get("server_enabled", True),
        connection_state=case.get("connection_state", "ready"),
    )
    store.create_server(server)
    snapshot = Snapshot(
        id="snapshot-1",
        server_id=server.id,
        version=1,
        schema_hash="b" * 64,
        discovered_at=datetime.now(UTC),
        tool_count=1,
    )
    review_state = case.get("review_state", "approved")
    tool = Tool(
        snapshot_id=snapshot.id,
        server_id=server.id,
        upstream_name="browser_navigate",
        model_alias="mcp__browser__browser_navigate",
        input_schema=SCHEMA,
        schema_hash=SCHEMA_HASH,
        metadata={"action": "navigate", "browser": True},
        enabled=case.get("tool_enabled", True),
        review_state=review_state,
        reviewed_at=datetime.now(UTC) if review_state == "approved" else None,
    )
    store.create_snapshot(snapshot, tools=(tool,))
    store.activate_snapshot(server.id, snapshot.id)
    if review_state == "approved":
        discovered = store.list_tools(snapshot.id)[0]
        store.update_tool(
            snapshot.id,
            discovered.upstream_name,
            expected_revision=discovered.revision,
            review_state="approved",
        )
    effect = case.get("effect")
    if effect is not None:
        store.create_policy_rule(
            PolicyRule(
                id=f"rule-{effect}",
                server_id=server.id,
                tool_name=tool.upstream_name,
                effect=effect,
                schemes=("https",),
                domains=tuple(case.get("domains", ("jobs.example.com",))),
                actions=tuple(case.get("actions", ("navigate",))),
                parameter_constraints=case.get("parameter_constraints", {}),
                data_classes=tuple(case.get("rule_data_classes", ())),
                schema_hash=SCHEMA_HASH,
                created_by="test",
            )
        )
    gate = PreToolCallGate(
        store,
        browser_policy=BrowserScopePolicy(resolver=_public_resolver),
    )
    executor = UnifiedToolExecutor(store, gate=gate)
    invocations: list[dict[str, Any]] = []

    async def invoke(arguments, _context):
        invocations.append(dict(arguments))
        return {"ok": True}

    executor.register_invoker(
        server_id=server.id,
        tool_name=tool.upstream_name,
        invoker=invoke,
        network_guard=lambda request: NetworkGuardAttestation(
            targets=(request.arguments["url"],),
            dns_pinned=True,
            redirects_enforced=True,
            peer_verified=True,
        ),
    )
    request = ToolCallRequest(
        caller="model",
        session_id=f"session-{case['name']}",
        turn_id="turn-1",
        call_id="call-1",
        server_id=server.id,
        tool_name=tool.upstream_name,
        snapshot_id=snapshot.id,
        schema_hash=SCHEMA_HASH,
        arguments=case.get(
            "arguments",
            {"url": "https://jobs.example.com/opening", "timeout": 5},
        ),
        data_classes=tuple(case.get("request_data_classes", ("job_keywords",))),
    )
    return gate, executor, request, invocations


CASES = [
    {
        "name": "server-disabled",
        "server_enabled": False,
        "effect": "allowlist_auto",
        "expected": ("deny", "server_disabled"),
    },
    {
        "name": "server-unavailable",
        "connection_state": "closed",
        "effect": "allowlist_auto",
        "expected": ("deny", "server_not_connected"),
    },
    {
        "name": "tool-disabled",
        "tool_enabled": False,
        "effect": "allowlist_auto",
        "expected": ("deny", "tool_disabled"),
    },
    {
        "name": "tool-unreviewed",
        "review_state": "unreviewed",
        "effect": "allowlist_auto",
        "expected": ("deny", "tool_review_required"),
    },
    {
        "name": "deny-wins",
        "effect": "deny",
        "expected": ("deny", "policy_deny"),
    },
    {
        "name": "always-confirm",
        "effect": "always_confirm",
        "expected": ("require_confirmation", "always_confirm"),
    },
    {
        "name": "confirm-once",
        "effect": "confirm_once",
        "expected": ("require_confirmation", "confirm_once"),
    },
    {
        "name": "domain-out-of-scope",
        "effect": "allowlist_auto",
        "domains": ("careers.example.net",),
        "expected": ("require_confirmation", "confirmation_required"),
    },
    {
        "name": "action-out-of-scope",
        "effect": "allowlist_auto",
        "actions": ("read",),
        "expected": ("require_confirmation", "confirmation_required"),
    },
    {
        "name": "parameter-out-of-scope",
        "effect": "allowlist_auto",
        "parameter_constraints": {"timeout": {"maximum": 3}},
        "expected": ("require_confirmation", "confirmation_required"),
    },
    {
        "name": "data-class-out-of-scope",
        "effect": "allowlist_auto",
        "rule_data_classes": ("location",),
        "expected": ("require_confirmation", "confirmation_required"),
    },
    {
        "name": "allowlisted",
        "effect": "allowlist_auto",
        "rule_data_classes": ("job_keywords",),
        "expected": ("allow", "allowlist_auto"),
    },
]


@pytest.mark.asyncio
@pytest.mark.parametrize("case", CASES, ids=lambda case: case["name"])
async def test_governance_decision_matrix_never_invokes_before_permit(
    case: dict[str, Any],
) -> None:
    gate, executor, request, invocations = _boundary(case)

    decision = await gate.evaluate(request)

    assert (decision.outcome, decision.reason_code) == case["expected"]
    assert invocations == []
    if decision.outcome == "allow":
        assert decision.permit is not None
        await executor.execute(request, permit_id=decision.permit.id)
        assert invocations == [dict(request.arguments)]
    else:
        assert decision.permit is None
        assert invocations == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("arguments", "data_classes", "reason"),
    [
        ({"url": "https://jobs.example.com/opening", "timeout": 31}, (), "invalid_arguments"),
        (
            {"url": "https://jobs.example.com/opening"},
            ("resume",),
            "sensitive_outbound",
        ),
        (
            {"url": "https://jobs.example.com/opening?email=person@example.com"},
            (),
            "sensitive_url_query",
        ),
    ],
)
async def test_argument_and_data_class_denials_do_not_reach_invoker(
    arguments: dict[str, Any],
    data_classes: tuple[str, ...],
    reason: str,
) -> None:
    case = {
        "name": reason,
        "effect": "allowlist_auto",
        "arguments": arguments,
        "request_data_classes": data_classes,
    }
    gate, _executor, request, invocations = _boundary(case)

    decision = await gate.evaluate(request)

    assert (decision.outcome, decision.reason_code) == ("deny", reason)
    assert decision.permit is None
    assert invocations == []
