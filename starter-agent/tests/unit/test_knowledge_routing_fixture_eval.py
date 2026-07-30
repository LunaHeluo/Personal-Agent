from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from starter_agent.trust.baseline import run_job_research_fixture_baseline
from starter_agent.trust.fixtures import JobResearchFixtureLoader
from starter_agent.trust.models import EvalCaseResult, TrustTraceEvent
from starter_agent.trust.rules import RuleAssertion, RuleEvaluator
from starter_agent.trust.store import TrustStore


PROJECT_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_ROOT = PROJECT_ROOT / "evals" / "job-research" / "fixtures"


def event(event_id: str, event_type: str, summary: dict) -> TrustTraceEvent:
    return TrustTraceEvent(
        id=event_id,
        eval_run_id="routing-rule-run",
        case_id="routing-rule-case",
        event_type=event_type,
        status="completed",
        occurred_at=datetime(2026, 7, 27, tzinfo=UTC),
        summary=summary,
        payload_hash="a" * 64,
    )


def test_routing_fixture_has_all_scoped_scenarios() -> None:
    fixture = JobResearchFixtureLoader(FIXTURE_ROOT).load_manifest().by_id(
        "knowledge-routing-redacted-v1"
    )

    assert {item["case_id"] for item in fixture.data["scenarios"]} == {
        "jr-conversation-greeting-no-tools",
        "jr-conversation-smalltalk-no-tools",
        "jr-job-knowledge-hit-no-network",
        "jr-job-resume-only-searches-and-reads-jd",
        "jr-job-no-profile-fails-closed",
        "jr-job-search-tool-disabled",
        "jr-job-browser-unavailable",
        "jr-knowledge-fact-no-web-fallback",
        "JR-ROUTE-FLEXIBLE-001",
        "JR-KB-MATCH-001",
        "JR-KB-LOCATION-MISS-001",
        "JR-KB-ROLE-MISS-001",
        "JR-KB-EXPIRED-001",
        "JR-LATEST-001",
        "JR-URL-FALLTHROUGH-001",
        "JR-MULTI-URL-001",
        "JR-LEGACY-SCHEMA-ABSENT-001",
        "JR-INJECTION-WEB-001",
    }


def test_routing_rules_reject_non_empty_callable_tools_and_wrong_counts() -> None:
    result = EvalCaseResult(
        id="routing-rule-result",
        run_id="routing-rule-run",
        case_id="routing-rule-case",
        status="passed",
        outcome_summary={"task_success": True, "route": "conversation"},
    )
    assertions = [
        RuleAssertion(
            id="route",
            kind="route",
            expected={"route": "conversation"},
        ),
        RuleAssertion(
            id="knowledge-count",
            kind="event_count",
            expected={"event_type": "Knowledge", "count": 0},
        ),
        RuleAssertion(
            id="tools-empty",
            kind="callable_tools_empty",
            expected={},
        ),
    ]
    evaluator = RuleEvaluator()

    clean = evaluator.evaluate(
        run_id="routing-rule-run",
        case_result=result,
        assertions=assertions,
        trace_events=[event("model-clean", "Model", {"callable_tools": []})],
    )
    mutated = evaluator.evaluate(
        run_id="routing-rule-run",
        case_result=result,
        assertions=assertions,
        trace_events=[
            event(
                "model-mutated",
                "Model",
                {"callable_tools": [{"name": "search_jobs_serpapi"}]},
            ),
            event("knowledge-mutated", "Knowledge", {"operation": "answer"}),
        ],
    )

    assert [item.status for item in clean] == ["passed", "passed", "passed"]
    assert [item.status for item in mutated] == ["passed", "failed", "failed"]


def test_fixed_runner_persists_executable_routing_events_and_assertions(
    tmp_path: Path,
) -> None:
    store = TrustStore(
        f"sqlite:///{tmp_path / 'routing-eval.db'}",
        PROJECT_ROOT,
    )

    run_job_research_fixture_baseline(
        store=store,
        project_root=PROJECT_ROOT,
        run_id=f"routing-eval-{uuid4().hex}",
        report_dir=tmp_path,
    )
    run = store.list_runs(run_type="fixture", limit=1)[0]
    events = store.list_trace_events(
        eval_run_id=run.id,
        case_id="jr-job-resume-only-searches-and-reads-jd",
    )
    result = next(
        item
        for item in store.list_case_results(run_id=run.id)
        if item.case_id == "jr-job-resume-only-searches-and-reads-jd"
    )
    assertions = store.list_assertion_results(
        case_result_id=result.id,
    )

    assert [item.event_type for item in events] == [
        "Route",
        "Knowledge",
        "Policy",
        "Tool",
        "Policy",
        "Tool",
        "Tool",
        "Tool",
        "Tool",
        "Tool",
    ]
    assert all(item.status == "passed" for item in assertions)
    assert result.outcome_summary["source_url"] == (
        "https://jobs.example.test/ai-agent"
    )
