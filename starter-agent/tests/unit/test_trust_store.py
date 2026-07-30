from datetime import UTC, datetime
from pathlib import Path
import sqlite3
from uuid import uuid4

import pytest

from starter_agent.capabilities.models import canonical_json_sha256
from starter_agent.trust.models import (
    EvalAssertionResult,
    EvalCase,
    EvalCaseResult,
    EvalFailureCluster,
    EvalFixture,
    EvalMetric,
    EvalReleaseGate,
    EvalRun,
    EvalSuite,
    SmokeRun,
    TrustTraceEvent,
)
from starter_agent.trust.store import PayloadConflictError, TrustStore


HASH = "c" * 64


PROJECT_ROOT = Path(__file__).parents[2]
SESSION_ONLY_ROOT = PROJECT_ROOT / ".session-only-trust-store-tests"


def _session_only_database_url(name: str) -> tuple[str, Path]:
    root = SESSION_ONLY_ROOT / f"{name}-{uuid4().hex}"
    root.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{root / 'agent.db'}", root


def test_trust_store_is_additive_and_links_eval_records() -> None:
    database_url, project_root = _session_only_database_url("linked")
    now = datetime.now(UTC)
    database_path = Path(database_url.removeprefix("sqlite:///"))
    with sqlite3.connect(database_path) as connection:
        connection.execute("CREATE TABLE existing_sessions (id TEXT PRIMARY KEY)")

    suite = EvalSuite(
        id="job-research-regression",
        name="Job Research Regression",
        version="2026.07.26",
        created_at=now,
        case_ids=("case-happy-path",),
        metadata_summary={"owner": "trust"},
    )
    fixture = EvalFixture(
        id="fixture-jd-1",
        fixture_type="jd_page",
        version="v1",
        manifest_hash=HASH,
        content_hash=canonical_json_sha256({"jd": "redacted"}),
        source_ref="fixtures/jd/redacted-greenhouse.json",
        summary={"title": "AI Agent Engineer", "redacted": True},
        redaction_summary={"secrets": "none", "resume": "chunked"},
    )
    case = EvalCase(
        id="case-happy-path",
        suite_id=suite.id,
        version="v1",
        layer="Happy Path",
        input_summary={"prompt": "compare public JD with resume evidence"},
        fixture_ids=(fixture.id,),
        expected_outcome={"status": "success"},
        expected_tool_calls=(
            {
                "tool": "search_jobs_serpapi",
                "arguments": {"query": "AI Agent Engineer", "limit": 3},
            },
        ),
        deterministic_assertions=(
            "tool_called:search_jobs_serpapi",
            "citation_required:resume_chunk",
        ),
        judge_rubric={"semantic_quality": "optional"},
        safety_level="standard",
    )
    run = EvalRun(
        id="run-fixture-1",
        suite_id=suite.id,
        run_type="fixture",
        status="running",
        started_at=now,
        code_version="abc123",
        code_dirty=True,
        prompt_version="prompt-v1",
        skill_version="job-research@1.1.0",
        tool_schema_version=HASH,
        policy_version="policy-v1",
        fixture_manifest_hash=fixture.manifest_hash,
    )
    case_result = EvalCaseResult(
        id="result-case-happy-path",
        run_id=run.id,
        case_id=case.id,
        status="passed",
        outcome_summary={"status": "success"},
        session_id="session-1",
        turn_id="turn-1",
        trace_event_ids=("trace-tool-1",),
    )
    assertion = EvalAssertionResult(
        id="assertion-tool-called",
        run_id=run.id,
        case_result_id=case_result.id,
        assertion_id="tool_called:search_jobs_serpapi",
        status="passed",
        expected_summary={"tool": "search_jobs_serpapi"},
        actual_summary={"tool": "search_jobs_serpapi"},
    )
    metric = EvalMetric(
        id="metric-tool-accuracy",
        run_id=run.id,
        name="Tool / Argument Accuracy",
        value=1.0,
        numerator=1.0,
        denominator=1.0,
        unit="ratio",
    )
    cluster = EvalFailureCluster(
        id="cluster-citations",
        run_id=run.id,
        cluster_key="citation-missing",
        title="Citation missing",
        case_result_ids=(case_result.id,),
        root_cause_summary={"component": "citation"},
        evidence_trace_event_ids=("trace-tool-1",),
    )
    gate = EvalReleaseGate(
        id="gate-run-fixture-1",
        run_id=run.id,
        status="blocked",
        safety_blocking=True,
        blocking_reasons=("safety hard gate failed",),
        metric_snapshot={"Task Success": 0.9, "Approval Compliance": 1.0},
    )
    trace_event = TrustTraceEvent(
        id="trace-tool-1",
        eval_run_id=run.id,
        case_id=case.id,
        session_id="session-1",
        turn_id="turn-1",
        model_request_id="model-request-1",
        tool_call_id="tool-call-1",
        policy_decision_id="policy-decision-1",
        approval_id="approval-1",
        parent_event_id=None,
        event_type="Tool",
        status="completed",
        occurred_at=now,
        summary={"tool": "search_jobs_serpapi", "status": "completed"},
        payload_hash=canonical_json_sha256({"tool": "search_jobs_serpapi"}),
    )
    smoke_run = EvalRun(
        id="run-smoke-1",
        suite_id=suite.id,
        run_type="smoke",
        status="passed",
        started_at=now,
        completed_at=now,
        code_version="abc123",
        code_dirty=True,
        prompt_version="prompt-v1",
        skill_version="job-research@1.1.0",
        tool_schema_version=HASH,
        policy_version="policy-v1",
        fixture_manifest_hash=None,
    )
    smoke = SmokeRun(
        id="smoke-public-jd-1",
        run_id=smoke_run.id,
        source_url="https://example.com/jobs/ai-agent-engineer",
        source_url_hash=canonical_json_sha256(
            {"url": "https://example.com/jobs/ai-agent-engineer"}
        ),
        trace_event_ids=("trace-smoke-1",),
        report_summary={"separate_from_fixture_baseline": True},
    )

    store = TrustStore(database_url, project_root)
    store.create_suite(suite)
    store.create_fixture(fixture)
    store.create_case(case)
    store.create_run(run)
    store.create_case_result(case_result)
    store.create_assertion_result(assertion)
    store.create_metric(metric)
    store.create_failure_cluster(cluster)
    store.create_release_gate(gate)
    store.append_trace_event(trace_event)
    store.create_run(smoke_run)
    store.create_smoke_run(smoke)
    store.close()

    reopened = TrustStore(database_url, project_root)

    assert reopened.get_suite(suite.id) == suite
    assert reopened.get_fixture(fixture.id) == fixture
    assert reopened.get_case(case.id) == case
    assert reopened.get_run(run.id) == run
    assert reopened.list_runs(run_type="fixture") == [run]
    assert reopened.list_runs(run_type="smoke") == [smoke_run]
    assert reopened.list_case_results(run_id=run.id) == [case_result]
    assert reopened.list_assertion_results(case_result_id=case_result.id) == [
        assertion
    ]
    assert reopened.list_metrics(run_id=run.id) == [metric]
    assert reopened.list_failure_clusters(run_id=run.id) == [cluster]
    assert reopened.get_release_gate(run.id) == gate
    assert reopened.list_trace_events(eval_run_id=run.id) == [trace_event]
    assert reopened.get_smoke_run(smoke.id) == smoke
    with sqlite3.connect(database_path) as connection:
        existing = connection.execute(
            "SELECT name FROM sqlite_master WHERE name = 'existing_sessions'"
        ).fetchone()
    assert existing == ("existing_sessions",)
    reopened.close()


def test_trace_events_are_idempotent_and_conflicts_are_stable() -> None:
    store = TrustStore("sqlite:///:memory:", PROJECT_ROOT)
    now = datetime.now(UTC)
    event = TrustTraceEvent(
        id="trace-1",
        eval_run_id="run-1",
        event_type="Policy",
        status="blocked",
        occurred_at=now,
        summary={"policy": "deny external send"},
        payload_hash=canonical_json_sha256({"policy": "deny external send"}),
    )

    first = store.append_trace_event(event)
    duplicate = store.append_trace_event(event)
    changed = event.model_copy(
        update={
            "summary": {"policy": "allow external send"},
            "payload_hash": canonical_json_sha256(
                {"policy": "allow external send"}
            ),
        }
    )

    assert first == duplicate
    with pytest.raises(PayloadConflictError):
        store.append_trace_event(changed)


def test_trust_summaries_reject_unredacted_secrets() -> None:
    with pytest.raises(ValueError):
        TrustTraceEvent(
            id="trace-secret",
            eval_run_id="run-1",
            event_type="Tool",
            status="completed",
            occurred_at=datetime.now(UTC),
            summary={"Authorization": "Bearer TEST-SECRET-DO-NOT-USE-123"},
            payload_hash=HASH,
        )
