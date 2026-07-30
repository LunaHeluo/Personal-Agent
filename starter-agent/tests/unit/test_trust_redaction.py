from starter_agent.trust.redaction import redact_trust_payload
from starter_agent.trust.store import TrustStore
from starter_agent.trust.trace import TraceContext, TrustTraceRecorder


FAKE_TOKEN = "Bearer sk-test-TRUST-LEAK-REGRESSION-123456"


def test_trust_redaction_happens_before_trace_write() -> None:
    store = TrustStore("sqlite:///:memory:", ".")
    event = TrustTraceRecorder(store).record(
        id="trace-redaction-1",
        context=TraceContext(eval_run_id="run-1", case_id="case-1"),
        event_type="Tool",
        status="completed",
        summary={
            "tool_name": "mcp__playwright__browser_snapshot",
            "Authorization": FAKE_TOKEN,
            "nested": {"cookie": "session=TEST-COOKIE-SHOULD-NOT-PERSIST"},
        },
        payload={"raw_result": f"token={FAKE_TOKEN}"},
    )

    serialized_event = event.model_dump_json()
    serialized_store = store.list_trace_events(eval_run_id="run-1")[0].model_dump_json()

    assert "TRUST-LEAK-REGRESSION" not in serialized_event
    assert "TEST-COOKIE-SHOULD-NOT-PERSIST" not in serialized_event
    assert "TRUST-LEAK-REGRESSION" not in serialized_store
    assert event.summary["Authorization"] == "<redacted>"
    assert event.summary["nested"]["cookie"] == "<redacted>"


def test_redaction_preserves_safe_eval_summary_shape() -> None:
    redacted = redact_trust_payload(
        {
            "source_url": "https://jobs.example.org/ai-agent-engineer",
            "title": "AI Agent Engineer",
            "api_token": "secret=TEST-VALUE",
        }
    )

    assert redacted == {
        "source_url": "https://jobs.example.org/ai-agent-engineer",
        "title": "AI Agent Engineer",
        "api_token": "<redacted>",
    }
