from datetime import UTC, datetime, timedelta
from pathlib import Path
import sqlite3

import pytest

from starter_agent.capabilities.models import (
    AuditEvent,
    Confirmation,
    ExecutionPermit,
    PolicyRule,
    Prompt,
    Resource,
    Server,
    SkillRecord,
    Snapshot,
    Tool,
)
from starter_agent.capabilities.store import CapabilityStore, RevisionConflictError


HASH = "b" * 64


def _server() -> Server:
    return Server(
        id="playwright",
        name="playwright",
        config_source="config/mcp.json",
        config_hash=HASH,
    )


def _confirmation(now: datetime) -> Confirmation:
    return Confirmation(
        id="confirmation-1",
        principal="local-user",
        session_id="session-1",
        turn_id="turn-1",
        call_id="call-1",
        request_hash=HASH,
        server_id="playwright",
        tool_name="browser_navigate",
        schema_hash=HASH,
        arguments_summary={"url": "https://example.test/job"},
        risk="external",
        destination="example.test",
        expires_at=now + timedelta(minutes=5),
    )


def test_store_is_additive_and_persists_governance_records_across_reopen(
    tmp_path: Path,
) -> None:
    database_url = f"sqlite:///{tmp_path / 'agent.db'}"
    now = datetime.now(UTC)
    with sqlite3.connect(tmp_path / "agent.db") as connection:
        connection.execute("CREATE TABLE existing_sessions (id TEXT PRIMARY KEY)")
    snapshot = Snapshot(
        id="snapshot-1",
        server_id="playwright",
        version=1,
        schema_hash=HASH,
        discovered_at=now,
        tool_count=1,
        resource_count=1,
        prompt_count=1,
    )
    tool = Tool(
        snapshot_id=snapshot.id,
        server_id="playwright",
        upstream_name="browser_navigate",
        model_alias="mcp__playwright__browser_navigate",
        input_schema={"type": "object", "additionalProperties": False},
        schema_hash=HASH,
        risk_level="external",
    )
    rule = PolicyRule(
        id="rule-1",
        server_id="playwright",
        tool_name=tool.upstream_name,
        effect="require_confirmation",
        created_by="local-admin",
    )
    skill = SkillRecord(
        name="job-research",
        source_path="skills/job-research/SKILL.md",
        version="1.0.0",
        updated_at=now,
        load_state="dependency_unavailable",
        snapshot_hash=HASH,
    )
    event = AuditEvent(
        event_id="event-1",
        actor="local-admin",
        action="server.created",
        target="server:playwright",
        after_hash=HASH,
        decision="allow",
        reason_code="initial_configuration",
        created_at=now,
    )
    resource = Resource(
        snapshot_id=snapshot.id,
        server_id="playwright",
        name="page",
        uri="browser://page",
    )
    prompt = Prompt(
        snapshot_id=snapshot.id,
        server_id="playwright",
        name="inspect_page",
        arguments=({"name": "url", "required": True},),
    )
    permit = ExecutionPermit(
        id="permit-1",
        confirmation_id="confirmation-1",
        request_hash=HASH,
        policy_revision=rule.revision,
        expires_at=now + timedelta(minutes=5),
    )
    store = CapabilityStore(database_url, tmp_path)
    store.create_server(_server())
    store.create_snapshot(
        snapshot,
        tools=[tool],
        resources=[resource],
        prompts=[prompt],
    )
    store.activate_snapshot("playwright", snapshot.id)
    store.create_policy_rule(rule)
    store.create_confirmation(_confirmation(now))
    store.create_execution_permit(permit)
    store.create_skill(skill)
    store.append_audit_event(event)
    store.close()

    reopened = CapabilityStore(database_url, tmp_path)

    assert reopened.get_server("playwright") == _server()
    assert reopened.get_snapshot(snapshot.id).active is True
    assert reopened.list_tools(snapshot.id) == [tool]
    assert reopened.list_resources(snapshot.id) == [resource]
    assert reopened.list_prompts(snapshot.id) == [prompt]
    assert reopened.get_policy_rule(rule.id) == rule
    assert reopened.get_confirmation("confirmation-1") == _confirmation(now)
    assert reopened.get_execution_permit(permit.id) == permit
    assert reopened.get_skill(skill.name) == skill
    assert reopened.list_audit_events() == [event]
    with sqlite3.connect(tmp_path / "agent.db") as connection:
        existing = connection.execute(
            "SELECT name FROM sqlite_master WHERE name = 'existing_sessions'"
        ).fetchone()
    assert existing == ("existing_sessions",)
    reopened.close()


def test_revision_conflicts_do_not_overwrite_newer_management_changes(
    tmp_path: Path,
) -> None:
    store = CapabilityStore("sqlite:///:memory:", tmp_path)
    store.create_server(_server())

    updated = store.update_server("playwright", expected_revision=0, enabled=False)

    assert updated.enabled is False
    assert updated.revision == 1
    with pytest.raises(RevisionConflictError):
        store.update_server("playwright", expected_revision=0, enabled=True)
    assert store.get_server("playwright") == updated


def test_confirmation_decisions_are_idempotent_and_policy_updates_are_revisioned(
    tmp_path: Path,
) -> None:
    store = CapabilityStore("sqlite:///:memory:", tmp_path)
    now = datetime.now(UTC)
    store.create_server(_server())
    store.create_confirmation(_confirmation(now))
    rule = PolicyRule(
        id="rule-1",
        server_id="playwright",
        tool_name="browser_navigate",
        effect="require_confirmation",
        created_by="local-admin",
    )
    store.create_policy_rule(rule)

    decided = store.decide_confirmation(
        "confirmation-1",
        expected_revision=0,
        idempotency_key="decision-request-1",
        decision="once",
    )
    duplicate = store.decide_confirmation(
        "confirmation-1",
        expected_revision=0,
        idempotency_key="decision-request-1",
        decision="once",
    )
    changed_rule = store.update_policy_rule(
        rule.id,
        expected_revision=0,
        enabled=False,
    )

    assert decided == duplicate
    assert decided.status == "approved"
    assert decided.revision == 1
    assert changed_rule.enabled is False
    assert changed_rule.revision == 1
    with pytest.raises(RevisionConflictError):
        store.update_policy_rule(rule.id, expected_revision=0, enabled=True)
    with pytest.raises(RevisionConflictError):
        store.decide_confirmation(
            "confirmation-1",
            expected_revision=0,
            idempotency_key="decision-request-2",
            decision="cancel",
        )
