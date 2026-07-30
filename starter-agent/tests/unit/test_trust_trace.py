from datetime import UTC, datetime

from starter_agent.capabilities.models import AuditEvent, canonical_json_sha256
from starter_agent.trust.store import TrustStore
from starter_agent.trust.trace import TraceContext, TrustTraceRecorder


def test_trace_recorder_persists_full_eval_correlation_chain() -> None:
    store = TrustStore("sqlite:///:memory:", ".")
    context = TraceContext(
        eval_run_id="run-1",
        case_id="case-1",
        session_id="session-1",
        turn_id="turn-1",
        model_request_id="model-request-1",
        tool_call_id="tool-call-1",
        policy_decision_id="policy-decision-1",
        approval_id="approval-1",
        child_run_id="child-run-1",
    )

    event = TrustTraceRecorder(store).record(
        id="trace-1",
        context=context,
        event_type="Tool",
        status="completed",
        summary={"tool_name": "search_jobs_serpapi"},
        payload={"tool_name": "search_jobs_serpapi", "arguments": {"limit": 3}},
    )

    assert event.eval_run_id == "run-1"
    assert event.case_id == "case-1"
    assert event.session_id == "session-1"
    assert event.turn_id == "turn-1"
    assert event.model_request_id == "model-request-1"
    assert event.tool_call_id == "tool-call-1"
    assert event.policy_decision_id == "policy-decision-1"
    assert event.approval_id == "approval-1"
    assert event.child_run_id == "child-run-1"
    assert event.payload_hash == canonical_json_sha256(
        {"tool_name": "search_jobs_serpapi", "arguments": {"limit": 3}}
    )
    assert store.list_trace_events(eval_run_id="run-1") == [event]


def test_trace_recorder_explains_missing_nodes_when_bridging_audit_event() -> None:
    store = TrustStore("sqlite:///:memory:", ".")
    audit_event = AuditEvent(
        event_id="audit-1",
        actor="agent",
        action="gate.evaluated",
        target="tool:search_jobs_serpapi",
        decision="allow",
        reason_code="allowlist_auto",
        created_at=datetime.now(UTC),
        payload={
            "session_id": "session-1",
            "turn_id": "turn-1",
            "tool_name": "search_jobs_serpapi",
        },
    )

    event = TrustTraceRecorder(store).from_audit_event(
        audit_event,
        context=TraceContext(eval_run_id="run-1", case_id="case-1"),
    )

    assert event.id == "audit-1"
    assert event.event_type == "Policy"
    assert event.policy_decision_id is None
    assert "missing_nodes" in event.summary
    assert event.summary["missing_nodes"]["policy_decision_id"] == (
        "not present in source audit event"
    )
    assert event.summary["tool_name"] == "search_jobs_serpapi"
