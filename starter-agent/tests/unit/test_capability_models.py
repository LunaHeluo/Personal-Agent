from datetime import UTC, datetime, timedelta, timezone
import hashlib
import json

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
HIGH_CONFIDENCE_SECRETS = (
    "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjMifQ.signature",
    "AIza" + "A" * 35,
    "xoxb-" + "A" * 10,
)


def _json_hash(value: object) -> str:
    serialized = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


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
    tool_schema = {
        "type": "object",
        "properties": {"url": {"type": "string"}},
        "required": ["url"],
        "additionalProperties": False,
    }
    tool = Tool(
        snapshot_id=snapshot.id,
        server_id=server.id,
        upstream_name="browser_navigate",
        model_alias="mcp__playwright__browser_navigate",
        description="Navigate to a public URL.",
        input_schema=tool_schema,
        schema_hash=_json_hash(tool_schema),
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


def test_confirmation_secret_detection_propagates_through_nested_containers() -> None:
    with pytest.raises(ValidationError, match="secret"):
        Confirmation(
            id="confirmation-nested",
            principal="local-user",
            session_id="session-1",
            turn_id="turn-1",
            call_id="call-1",
            request_hash=HASH,
            server_id="playwright",
            tool_name="browser_navigate",
            schema_hash=HASH,
            arguments_summary={
                "token": {
                    "details": [
                        {"value": "plaintext-that-must-not-persist"},
                    ]
                }
            },
            risk="external",
            destination="example.test",
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )


@pytest.mark.parametrize("secret_value", HIGH_CONFIDENCE_SECRETS)
def test_confirmation_summary_rejects_mcp_high_confidence_secret_shapes(
    secret_value: str,
) -> None:
    with pytest.raises(ValidationError, match="secret"):
        Confirmation(
            id="confirmation-secret-shape",
            principal="local-user",
            session_id="session-1",
            turn_id="turn-1",
            call_id="call-1",
            request_hash=HASH,
            server_id="playwright",
            tool_name="browser_navigate",
            schema_hash=HASH,
            arguments_summary={"details": {"value": secret_value}},
            risk="external",
            destination="example.test",
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )


@pytest.mark.parametrize("secret_value", HIGH_CONFIDENCE_SECRETS)
def test_audit_event_text_rejects_mcp_high_confidence_secret_shapes(
    secret_value: str,
) -> None:
    with pytest.raises(ValidationError, match="secret"):
        AuditEvent(
            event_id="event-secret-shape",
            actor="local-admin",
            action="server.created",
            target=f"server:playwright:{secret_value}",
            after_hash=HASH,
            decision="allow",
            reason_code="initial_configuration",
            created_at=datetime.now(UTC),
        )


@pytest.mark.parametrize("secret_value", HIGH_CONFIDENCE_SECRETS)
def test_audit_event_payload_rejects_mcp_high_confidence_secret_shapes(
    secret_value: str,
) -> None:
    with pytest.raises(ValidationError, match="secret"):
        AuditEvent(
            event_id="event-secret-payload",
            actor="local-admin",
            action="server.created",
            target="server:playwright",
            after_hash=HASH,
            decision="allow",
            reason_code="initial_configuration",
            payload={"details": {"value": secret_value}},
            created_at=datetime.now(UTC),
        )


@pytest.mark.parametrize(
    "field_name,secret_value",
    [
        ("event_id", "ghp_" + "A" * 36),
        ("actor", "AKIA" + "A" * 16),
        ("action", "Basic dXNlcjpwYXNzd29yZA=="),
        ("target", "https://user:password@example.test/job"),
        ("reason_code", "github_pat_" + "A" * 24),
        ("session_id", "ghp_" + "B" * 36),
        ("turn_id", "AKIA" + "B" * 16),
        ("call_id", "ghp_" + "C" * 36),
    ],
)
def test_audit_event_rejects_secrets_in_every_persisted_text_field(
    field_name: str,
    secret_value: str,
) -> None:
    values = {
        "event_id": "event-1",
        "actor": "local-admin",
        "action": "server.created",
        "target": "server:playwright",
        "after_hash": HASH,
        "decision": "allow",
        "reason_code": "initial_configuration",
        "session_id": "session-1",
        "turn_id": "turn-1",
        "call_id": "call-1",
        "created_at": datetime.now(UTC),
    }
    values[field_name] = secret_value

    with pytest.raises(ValidationError, match="secret"):
        AuditEvent.model_validate(values)


def test_audit_event_payload_accepts_safe_data_and_rejects_nested_secrets() -> None:
    values = {
        "event_id": "event-1",
        "actor": "local-admin",
        "action": "server.created",
        "target": "server:playwright",
        "after_hash": HASH,
        "decision": "allow",
        "reason_code": "initial_configuration",
        "created_at": datetime.now(UTC),
    }

    safe = AuditEvent.model_validate(
        {**values, "payload": {"details": {"record_count": 1}}}
    )

    assert safe.payload == {"details": {"record_count": 1}}
    with pytest.raises(ValidationError, match="secret"):
        AuditEvent.model_validate(
            {
                **values,
                "payload": {
                    "token": {
                        "details": [{"value": "plaintext-that-must-not-persist"}]
                    }
                },
            }
        )


@pytest.mark.parametrize(
    "overrides",
    [
        {"status": "pending", "decision": "once"},
        {"status": "pending", "idempotency_key_hash": HASH},
        {"status": "pending", "decided_at": datetime.now(UTC)},
        {
            "status": "approved",
            "decision": None,
            "idempotency_key_hash": HASH,
            "decided_at": datetime.now(UTC),
        },
        {
            "status": "approved",
            "decision": "cancel",
            "idempotency_key_hash": HASH,
            "decided_at": datetime.now(UTC),
        },
        {
            "status": "cancelled",
            "decision": "once",
            "idempotency_key_hash": HASH,
            "decided_at": datetime.now(UTC),
        },
        {
            "status": "expired",
            "decision": "once",
            "idempotency_key_hash": HASH,
            "decided_at": datetime.now(UTC),
        },
        {"status": "expired", "decided_at": None},
    ],
)
def test_confirmation_rejects_inconsistent_status_fields(
    overrides: dict[str, object],
) -> None:
    values = {
        "id": "confirmation-consistency",
        "principal": "local-user",
        "session_id": "session-1",
        "turn_id": "turn-1",
        "call_id": "call-1",
        "request_hash": HASH,
        "server_id": "playwright",
        "tool_name": "browser_navigate",
        "schema_hash": HASH,
        "arguments_summary": {"url": "https://example.test/job"},
        "risk": "external",
        "destination": "example.test",
        "expires_at": datetime.now(UTC) + timedelta(minutes=5),
    }

    with pytest.raises(ValidationError, match="status|decision|idempotency|decided"):
        Confirmation.model_validate({**values, **overrides})


def test_confirmation_accepts_consistent_terminal_states() -> None:
    now = datetime.now(UTC)
    values = {
        "id": "confirmation-consistency",
        "principal": "local-user",
        "session_id": "session-1",
        "turn_id": "turn-1",
        "call_id": "call-1",
        "request_hash": HASH,
        "server_id": "playwright",
        "tool_name": "browser_navigate",
        "schema_hash": HASH,
        "arguments_summary": {"url": "https://example.test/job"},
        "risk": "external",
        "destination": "example.test",
        "expires_at": now + timedelta(minutes=5),
        "decided_at": now,
        "revision": 1,
    }

    approved = Confirmation.model_validate(
        {
            **values,
            "status": "approved",
            "decision": "once",
            "idempotency_key_hash": HASH,
        }
    )
    cancelled = Confirmation.model_validate(
        {
            **values,
            "status": "cancelled",
            "decision": "cancel",
            "idempotency_key_hash": HASH,
        }
    )
    expired = Confirmation.model_validate({**values, "status": "expired"})

    assert approved.status == "approved"
    assert cancelled.status == "cancelled"
    assert expired.status == "expired"


def test_tool_schema_hash_is_bound_to_canonical_schema() -> None:
    schema = {
        "type": "object",
        "properties": {"url": {"type": "string"}},
        "required": ["url"],
        "additionalProperties": False,
    }

    with pytest.raises(ValidationError, match="schema_hash"):
        Tool(
            snapshot_id="snapshot-1",
            server_id="playwright",
            upstream_name="browser_navigate",
            model_alias="mcp__playwright__browser_navigate",
            input_schema=schema,
            schema_hash=HASH,
        )

    tool = Tool(
        snapshot_id="snapshot-1",
        server_id="playwright",
        upstream_name="browser_navigate",
        model_alias="mcp__playwright__browser_navigate",
        input_schema=schema,
        schema_hash=_json_hash(schema),
    )

    assert tool.schema_hash == _json_hash(tool.input_schema)


def test_json_payload_fields_are_immutable_defensive_and_round_trip() -> None:
    now = datetime.now(UTC)
    schema = {
        "type": "object",
        "properties": {"url": {"enum": ["https://example.test/job"]}},
        "additionalProperties": False,
    }
    shared = {"nested": {"items": [1]}}
    tool = Tool(
        snapshot_id="snapshot-1",
        server_id="playwright",
        upstream_name="browser_navigate",
        model_alias="mcp__playwright__browser_navigate",
        input_schema=schema,
        schema_hash=_json_hash(schema),
        metadata=shared,
    )
    resource = Resource(
        snapshot_id="snapshot-1",
        server_id="playwright",
        name="page",
        uri="browser://page",
        parameters=(shared,),
        metadata=shared,
    )
    prompt = Prompt(
        snapshot_id="snapshot-1",
        server_id="playwright",
        name="inspect_page",
        arguments=(shared,),
        metadata=shared,
    )
    rule = PolicyRule(
        id="rule-1",
        server_id="playwright",
        tool_name="browser_navigate",
        effect="require_confirmation",
        parameter_constraints=shared,
        created_by="local-admin",
    )
    confirmation = Confirmation(
        id="confirmation-json",
        principal="local-user",
        session_id="session-1",
        turn_id="turn-1",
        call_id="call-1",
        request_hash=HASH,
        server_id="playwright",
        tool_name="browser_navigate",
        schema_hash=_json_hash(schema),
        arguments_summary={"safe": shared},
        risk="external",
        destination="example.test",
        expires_at=now + timedelta(minutes=5),
    )
    audit = AuditEvent(
        event_id="event-json",
        actor="local-admin",
        action="server.created",
        target="server:playwright",
        decision="allow",
        reason_code="initial_configuration",
        payload={"safe": shared},
        created_at=now,
    )
    models = (tool, resource, prompt, rule, confirmation, audit)

    shared["nested"]["items"].append(2)
    schema["properties"]["url"]["enum"].append("https://mutated.test")

    immutable_payloads = (
        tool.input_schema,
        tool.metadata,
        resource.parameters[0],
        resource.metadata,
        prompt.arguments[0],
        prompt.metadata,
        rule.parameter_constraints,
        confirmation.arguments_summary,
        audit.payload,
    )
    for payload in immutable_payloads:
        assert "https://mutated.test" not in repr(payload)
        assert "2" not in repr(payload)
        with pytest.raises(TypeError):
            payload["mutated"] = True
    for model in models:
        restored = type(model).model_validate_json(model.model_dump_json())
        assert restored == model


def test_json_payload_fields_reject_non_json_non_finite_deep_and_large_values() -> None:
    deep: dict[str, object] = {}
    cursor = deep
    for _ in range(25):
        child: dict[str, object] = {}
        cursor["child"] = child
        cursor = child

    invalid_values = (
        b"bytes",
        {"set-value"},
        object(),
        float("nan"),
        deep,
        "x" * 300_000,
    )
    for invalid_value in invalid_values:
        with pytest.raises(ValidationError, match="JSON"):
            Tool(
                snapshot_id="snapshot-1",
                server_id="playwright",
                upstream_name="browser_navigate",
                model_alias="mcp__playwright__browser_navigate",
                input_schema={"value": invalid_value},
                schema_hash=HASH,
            )
