from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from starter_agent.capabilities.confirmations import (
    ConfirmationService,
    ConfirmationStateError,
)
from starter_agent.capabilities.gate import PreToolCallGate, ToolCallRequest
from starter_agent.capabilities.models import PolicyRule, canonical_json_sha256
from starter_agent.capabilities.store import CapabilityStore
from tests.unit.test_pre_tool_call_gate import SCHEMA_HASH, _public_resolver, _store
from starter_agent.capabilities.policy import BrowserScopePolicy


def _confirmation_gate(tmp_path, *, database_url="sqlite:///:memory:"):
    if database_url == "sqlite:///:memory:":
        store = _store()
    else:
        seeded = _store()
        store = CapabilityStore(database_url, tmp_path)
        server = seeded.get_server("playwright")
        snapshot = seeded.get_active_snapshot("playwright")
        assert server is not None and snapshot is not None
        store.create_server(server)
        store.create_snapshot(
            snapshot.model_copy(update={"active": False}),
            tools=seeded.list_tools(snapshot.id),
        )
        store.activate_snapshot(server.id, snapshot.id)
        discovered = store.list_tools(snapshot.id)[0]
        store.update_tool(
            snapshot.id,
            discovered.upstream_name,
            expected_revision=discovered.revision,
            review_state="approved",
        )
        for rule in seeded.list_policy_rules("playwright", "browser_navigate"):
            store.create_policy_rule(rule)
    allow = store.get_policy_rule("allow-nav")
    assert allow is not None
    store.update_policy_rule(allow.id, expected_revision=0, enabled=False)
    gate = PreToolCallGate(
        store,
        browser_policy=BrowserScopePolicy(resolver=_public_resolver),
    )
    return store, gate


def _request() -> ToolCallRequest:
    return ToolCallRequest(
        principal="local-user",
        caller="model",
        session_id="session-1",
        turn_id="turn-1",
        call_id="call-1",
        server_id="playwright",
        tool_name="browser_navigate",
        snapshot_id="snapshot-1",
        schema_hash=SCHEMA_HASH,
        arguments={"url": "https://jobs.example.com/opening"},
        data_classes=("job_keywords",),
    )


async def test_service_persists_safe_pending_before_resolution_and_audits(tmp_path) -> None:
    store, gate = _confirmation_gate(tmp_path)
    service = ConfirmationService(store, gate, confirmation_ttl_seconds=60)
    request = _request()
    decision = await gate.evaluate(request)
    assert decision.outcome == "require_confirmation"

    pending = service.create_pending(request, decision)

    assert store.get_confirmation(pending.id) == pending
    assert service.list_pending(session_id=request.session_id) == [pending]
    assert pending.arguments_hash == request.confirmation_arguments_hash
    assert pending.arguments_summary == decision.arguments_summary
    assert pending.policy_revision > 0
    assert pending.data_classes == request.data_classes
    assert pending.destination == decision.destination_summary
    assert pending.expires_at > datetime.now(UTC)
    assert [event.action for event in store.list_audit_events()] == [
        "confirmation.created"
    ]


async def test_decision_is_idempotent_and_restart_expires_orphaned_pending(tmp_path) -> None:
    database = tmp_path / "confirmations.db"
    durable, gate = _confirmation_gate(
        tmp_path, database_url=f"sqlite:///{database}"
    )
    service = ConfirmationService(durable, gate, confirmation_ttl_seconds=60)
    request = _request()
    decision = await gate.evaluate(request)
    pending = service.create_pending(request, decision)

    approved = service.decide(
        pending.id,
        expected_revision=0,
        idempotency_key="decision-key-1",
        decision="once",
    )
    replay = service.decide(
        pending.id,
        expected_revision=0,
        idempotency_key="decision-key-1",
        decision="once",
    )

    assert replay == approved
    assert approved.status == "approved"
    second = service.create_pending(
        request.model_copy(update={"call_id": "call-orphan"}),
        decision,
    )
    durable.close()
    reopened = CapabilityStore(f"sqlite:///{database}", tmp_path)
    restarted = ConfirmationService(reopened, gate, expire_orphans=True)

    assert restarted.get(second.id).status == "expired"
    actions = [event.action for event in reopened.list_audit_events()]
    assert actions.count("confirmation.decided") == 1
    assert "confirmation.expired" in actions


async def test_always_confirm_rejects_allowlist_without_creating_rule(tmp_path) -> None:
    store, gate = _confirmation_gate(tmp_path)
    store.create_policy_rule(
        PolicyRule(
            id="always-confirm-script",
            server_id="playwright",
            tool_name="browser_navigate",
            effect="always_confirm",
            schema_hash=SCHEMA_HASH,
            created_by="admin",
        )
    )
    service = ConfirmationService(store, gate)
    request = _request()
    gate_decision = await gate.evaluate(request)
    assert gate_decision.reason_code == "always_confirm"
    pending = service.create_pending(request, gate_decision)

    with pytest.raises(ConfirmationStateError, match="allowlist_forbidden"):
        service.decide(
            pending.id,
            expected_revision=0,
            idempotency_key="decision-key-allowlist",
            decision="allowlist",
        )

    assert service.get(pending.id).status == "pending"
    assert not any(
        rule.effect == "allowlist_auto"
        for rule in store.list_policy_rules(request.server_id, request.tool_name)
        if rule.enabled
    )


@pytest.mark.parametrize("policy_change", ["revision", "always_confirm"])
async def test_approved_confirmation_is_invalid_after_policy_change(
    tmp_path, policy_change
) -> None:
    store, gate = _confirmation_gate(tmp_path)
    service = ConfirmationService(store, gate)
    request = _request()
    pending = service.create_pending(request, await gate.evaluate(request))
    approved = service.decide(
        pending.id,
        expected_revision=0,
        idempotency_key=f"decision-{policy_change}",
        decision="once",
    )

    if policy_change == "revision":
        rule = store.get_policy_rule("allow-nav")
        assert rule is not None
        store.update_policy_rule(
            rule.id,
            expected_revision=rule.revision,
            created_by="changed-admin",
        )
    else:
        store.create_policy_rule(
            PolicyRule(
                id="new-always-confirm",
                server_id=request.server_id,
                tool_name=request.tool_name,
                effect="always_confirm",
                schema_hash=request.schema_hash,
                created_by="admin",
            )
        )

    result = await gate.evaluate_approved(request, confirmation_id=approved.id)

    assert (result.outcome, result.reason_code) == (
        "deny",
        "confirmation_policy_changed",
    )
