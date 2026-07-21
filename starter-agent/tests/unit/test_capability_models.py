from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

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


HASH = "a" * 64


def test_capability_models_define_bounded_governance_contracts() -> None:
    local_time = datetime(2026, 7, 21, 12, tzinfo=timezone(timedelta(hours=8)))
    server = Server(
        id="playwright",
        name="playwright",
        config_source="config/mcp.json",
        config_hash=HASH,
        connection_state="ready",
        health_state="healthy",
        operation_state="ready",
        runtime_name="playwright-mcp",
        runtime_version="1.0.0",
        last_checked_at=local_time,
    )
    snapshot = Snapshot(
        id="snapshot-1",
        server_id=server.id,
        version=1,
        schema_hash=HASH,
        discovered_at=local_time,
        tool_count=1,
    )
    tool = Tool(
        snapshot_id=snapshot.id,
        server_id=server.id,
        upstream_name="browser_navigate",
        model_alias="mcp__playwright__browser_navigate",
        description="Navigate to a public URL.",
        input_schema={
            "type": "object",
            "properties": {"url": {"type": "string"}},
            "required": ["url"],
            "additionalProperties": False,
        },
        schema_hash=HASH,
        risk_level="external",
        outbound_scope=("public_url",),
        enabled=False,
        review_state="unreviewed",
    )
    resource = Resource(
        snapshot_id=snapshot.id,
        server_id=server.id,
        name="page",
        uri="browser://page",
        enabled=False,
    )
    prompt = Prompt(
        snapshot_id=snapshot.id,
        server_id=server.id,
        name="inspect_page",
        arguments=({"name": "url", "required": True},),
        enabled=False,
    )
    rule = PolicyRule(
        id="rule-1",
        server_id=server.id,
        tool_name=tool.upstream_name,
        effect="require_confirmation",
        schemes=("https",),
        domains=("*",),
        created_by="local-admin",
        schema_hash=HASH,
    )
    confirmation = Confirmation(
        id="confirmation-1",
        principal="local-user",
        session_id="session-1",
        turn_id="turn-1",
        call_id="call-1",
        request_hash=HASH,
        server_id=server.id,
        tool_name=tool.upstream_name,
        schema_hash=HASH,
        arguments_summary={"url": "https://example.test/job"},
        risk="external",
        destination="example.test",
        expires_at=local_time + timedelta(minutes=5),
    )
    permit = ExecutionPermit(
        id="permit-1",
        confirmation_id=confirmation.id,
        request_hash=HASH,
        policy_revision=rule.revision,
        expires_at=confirmation.expires_at,
    )
    skill = SkillRecord(
        name="job-research",
        source_path="skills/job-research/SKILL.md",
        version="1.0.0",
        updated_at=local_time,
        load_state="dependency_unavailable",
        snapshot_hash=HASH,
        dependencies=("mcp:playwright", "tool:retrieve_resume_evidence"),
    )
    audit = AuditEvent(
        event_id="event-1",
        actor="local-admin",
        action="server.created",
        target="server:playwright",
        after_hash=HASH,
        decision="allow",
        reason_code="initial_configuration",
        created_at=local_time,
    )

    assert server.last_checked_at is not None
    assert server.last_checked_at.tzinfo is UTC
    assert snapshot.discovered_at.tzinfo is UTC
    assert tool.input_schema["additionalProperties"] is False
    assert tool.enabled is False
    assert resource.enabled is False
    assert prompt.enabled is False
    assert rule.revision == confirmation.revision == skill.revision == 0
    assert permit.consumed_at is None
    assert audit.created_at.tzinfo is UTC


def test_capability_models_reject_extra_fields_naive_times_and_invalid_hashes() -> None:
    common = {
        "id": "playwright",
        "name": "playwright",
        "config_source": "config/mcp.json",
        "config_hash": HASH,
    }

    with pytest.raises(ValidationError, match="extra_forbidden"):
        Server(**common, secret="must-not-be-stored")
    with pytest.raises(ValidationError):
        Snapshot(
            id="snapshot-1",
            server_id="playwright",
            version=1,
            schema_hash=HASH,
            discovered_at=datetime(2026, 7, 21),
        )
    with pytest.raises(ValidationError):
        Server(**{**common, "config_hash": "not-a-sha256"})
    with pytest.raises(ValidationError, match="uri"):
        Resource(
            snapshot_id="snapshot-1",
            server_id="playwright",
            name="missing-location",
        )
    with pytest.raises(ValidationError, match="secret"):
        Confirmation(
            id="confirmation-1",
            principal="local-user",
            session_id="session-1",
            turn_id="turn-1",
            call_id="call-1",
            request_hash=HASH,
            server_id="playwright",
            tool_name="browser_navigate",
            schema_hash=HASH,
            arguments_summary={"api_token": "inline-secret"},
            risk="external",
            destination="example.test",
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )
