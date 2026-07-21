from datetime import UTC, datetime, timedelta
from pathlib import Path
import sqlite3

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.sql.dml import Update

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
    canonical_json_sha256,
)
from starter_agent.capabilities.store import CapabilityStore, RevisionConflictError


HASH = "b" * 64


def _server(server_id: str = "playwright") -> Server:
    return Server(
        id=server_id,
        name=server_id,
        config_source="config/mcp.json",
        config_hash=HASH,
    )


def _confirmation(
    now: datetime,
    confirmation_id: str = "confirmation-1",
    *,
    expires_at: datetime | None = None,
) -> Confirmation:
    return Confirmation(
        id=confirmation_id,
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
        expires_at=expires_at or now + timedelta(minutes=5),
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
    tool_schema = {"type": "object", "additionalProperties": False}
    tool = Tool(
        snapshot_id=snapshot.id,
        server_id="playwright",
        upstream_name="browser_navigate",
        model_alias="mcp__playwright__browser_navigate",
        input_schema=tool_schema,
        schema_hash=canonical_json_sha256(tool_schema),
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
        payload={"details": {"record_count": 1, "sources": ["greenhouse"]}},
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


def test_confirmation_replay_binds_revision_key_and_decision(tmp_path: Path) -> None:
    store = CapabilityStore("sqlite:///:memory:", tmp_path)
    now = datetime.now(UTC)
    store.create_server(_server())
    store.create_confirmation(_confirmation(now))

    decided = store.decide_confirmation(
        "confirmation-1",
        expected_revision=0,
        idempotency_key="decision-request-1",
        decision="once",
    )

    assert (
        store.decide_confirmation(
            "confirmation-1",
            expected_revision=0,
            idempotency_key="decision-request-1",
            decision="once",
        )
        == decided
    )
    conflict_cases = (
        (1, "decision-request-1", "once"),
        (-1, "decision-request-1", "once"),
        (0, "decision-request-1", "cancel"),
        (0, "decision-request-2", "once"),
    )
    for expected_revision, idempotency_key, decision in conflict_cases:
        with pytest.raises(RevisionConflictError):
            store.decide_confirmation(
                "confirmation-1",
                expected_revision=expected_revision,
                idempotency_key=idempotency_key,
                decision=decision,
            )

    pending = _confirmation(now, "confirmation-stale")
    store.create_confirmation(pending)
    with pytest.raises(RevisionConflictError):
        store.decide_confirmation(
            pending.id,
            expected_revision=1,
            idempotency_key="stale-first-writer",
            decision="once",
        )
    assert store.get_confirmation(pending.id) == pending


def test_confirmation_expiry_uses_callers_revision_and_is_single_transition(
    tmp_path: Path,
) -> None:
    store = CapabilityStore("sqlite:///:memory:", tmp_path)
    now = datetime.now(UTC)
    store.create_server(_server())
    expired_pending = _confirmation(
        now,
        "confirmation-expired",
        expires_at=now - timedelta(seconds=1),
    )
    store.create_confirmation(expired_pending)

    with pytest.raises(RevisionConflictError):
        store.decide_confirmation(
            expired_pending.id,
            expected_revision=1,
            idempotency_key="expired-request",
            decision="once",
        )
    assert store.get_confirmation(expired_pending.id) == expired_pending

    expired = store.decide_confirmation(
        expired_pending.id,
        expected_revision=0,
        idempotency_key="expired-request",
        decision="once",
    )

    assert expired.status == "expired"
    assert expired.decision is None
    assert expired.idempotency_key_hash is None
    assert expired.revision == 1
    with pytest.raises(RevisionConflictError):
        store.decide_confirmation(
            expired_pending.id,
            expected_revision=0,
            idempotency_key="expired-request",
            decision="once",
        )


def test_confirmation_concurrent_replay_fallback_still_binds_revision(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database_url = f"sqlite:///{tmp_path / 'confirmation-race.db'}"
    primary = CapabilityStore(database_url, tmp_path)
    competing = CapabilityStore(database_url, tmp_path)
    now = datetime.now(UTC)
    primary.create_server(_server())
    primary.create_confirmation(_confirmation(now))
    original_execute = Session.execute
    race_triggered = False

    def execute_with_competing_winner(self, statement, *args, **kwargs):
        nonlocal race_triggered
        if (
            isinstance(statement, Update)
            and statement.table.name == "tool_confirmations"
            and not race_triggered
        ):
            race_triggered = True
            competing.decide_confirmation(
                "confirmation-1",
                expected_revision=0,
                idempotency_key="decision-request-1",
                decision="once",
            )
        return original_execute(self, statement, *args, **kwargs)

    monkeypatch.setattr(Session, "execute", execute_with_competing_winner)

    with pytest.raises(RevisionConflictError):
        primary.decide_confirmation(
            "confirmation-1",
            expected_revision=1,
            idempotency_key="decision-request-1",
            decision="once",
        )
    assert race_triggered is True


def test_snapshot_creation_requires_inactive_state(tmp_path: Path) -> None:
    store = CapabilityStore("sqlite:///:memory:", tmp_path)
    store.create_server(_server())
    active = Snapshot(
        id="snapshot-active",
        server_id="playwright",
        version=1,
        schema_hash=HASH,
        discovered_at=datetime.now(UTC),
        active=True,
    )

    with pytest.raises(ValueError, match="inactive"):
        store.create_snapshot(active)
    assert store.get_snapshot(active.id) is None


def test_snapshot_activation_is_unique_per_server_and_isolates_servers(
    tmp_path: Path,
) -> None:
    store = CapabilityStore("sqlite:///:memory:", tmp_path)
    store.create_server(_server())
    store.create_server(_server("secondary"))
    now = datetime.now(UTC)
    first = Snapshot(
        id="snapshot-first",
        server_id="playwright",
        version=1,
        schema_hash=HASH,
        discovered_at=now,
    )
    second = Snapshot(
        id="snapshot-second",
        server_id="playwright",
        version=2,
        schema_hash="c" * 64,
        discovered_at=now,
    )
    other_server = Snapshot(
        id="snapshot-secondary",
        server_id="secondary",
        version=1,
        schema_hash="d" * 64,
        discovered_at=now,
    )
    store.create_snapshot(first)
    store.create_snapshot(second)
    store.create_snapshot(other_server)
    store.activate_snapshot(first.server_id, first.id)
    store.activate_snapshot(other_server.server_id, other_server.id)

    store.activate_snapshot(second.server_id, second.id)

    assert store.get_snapshot(first.id).active is False
    assert store.get_snapshot(second.id).active is True
    assert store.get_snapshot(other_server.id).active is True


def test_snapshot_partial_unique_index_blocks_two_active_rows(tmp_path: Path) -> None:
    store = CapabilityStore("sqlite:///:memory:", tmp_path)
    store.create_server(_server())
    now = datetime.now(UTC)
    first = Snapshot(
        id="snapshot-first",
        server_id="playwright",
        version=1,
        schema_hash=HASH,
        discovered_at=now,
    )
    second = Snapshot(
        id="snapshot-second",
        server_id="playwright",
        version=2,
        schema_hash="c" * 64,
        discovered_at=now,
    )
    store.create_snapshot(first)
    store.create_snapshot(second)
    store.activate_snapshot(first.server_id, first.id)

    with pytest.raises(IntegrityError):
        with store.engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE mcp_capability_snapshots "
                    "SET active = 1 WHERE id = :snapshot_id"
                ),
                {"snapshot_id": second.id},
            )


def test_skill_revision_update_is_persisted_and_stale_write_conflicts(
    tmp_path: Path,
) -> None:
    store = CapabilityStore("sqlite:///:memory:", tmp_path)
    skill = SkillRecord(
        name="job-research",
        source_path="skills/job-research/SKILL.md",
        version="1.0.0",
        updated_at=datetime.now(UTC),
        load_state="dependency_unavailable",
        snapshot_hash=HASH,
    )
    store.create_skill(skill)

    updated = store.update_skill(
        skill.name,
        expected_revision=0,
        enabled=True,
        load_state="loaded",
    )

    assert updated.enabled is True
    assert updated.load_state == "loaded"
    assert updated.revision == 1
    assert store.get_skill(skill.name) == updated
    with pytest.raises(RevisionConflictError):
        store.update_skill(skill.name, expected_revision=0, enabled=False)
