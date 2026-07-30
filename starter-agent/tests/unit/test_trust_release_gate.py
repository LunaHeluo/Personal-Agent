from starter_agent.trust.models import (
    EvalAssertionResult,
    EvalCaseResult,
    EvalMetric,
)
from starter_agent.trust.release_gate import (
    FailureClusterer,
    ReleaseGateDecider,
    RunComparator,
)


def _case_result(case_id: str, status: str, error_code: str | None = None) -> EvalCaseResult:
    summary = {} if error_code is None else {"error_code": error_code}
    return EvalCaseResult(
        id=f"result-{case_id}",
        run_id="run-1",
        case_id=case_id,
        status=status,
        outcome_summary=summary,
    )


def _assertion(assertion_id: str, status: str, *, hard: bool = False) -> EvalAssertionResult:
    return EvalAssertionResult(
        id=f"assertion-{assertion_id}",
        run_id="run-1",
        case_result_id="result-case-a",
        assertion_id=assertion_id,
        status=status,
        expected_summary={"assertion": assertion_id},
        actual_summary={
            "reason": "policy order failed",
            "safety_hard_gate": hard,
        },
    )


def test_failure_clusterer_groups_case_and_assertion_failures_with_trace_evidence() -> None:
    clusters = FailureClusterer().cluster(
        run_id="run-1",
        case_results=[
            _case_result("case-a", "error", "case_timeout"),
            _case_result("case-b", "error", "case_timeout"),
        ],
        assertion_results=[_assertion("approval:before-tool", "blocked", hard=True)],
    )

    by_key = {cluster.cluster_key: cluster for cluster in clusters}

    assert by_key["case_error:case_timeout"].case_result_ids == (
        "result-case-a",
        "result-case-b",
    )
    assert by_key["assertion:approval:blocked"].root_cause_summary["safety_hard_gate"] is True


def test_release_gate_blocks_on_safety_hard_failure_even_with_good_average() -> None:
    gate = ReleaseGateDecider().decide(
        run_id="run-1",
        metrics=[
            EvalMetric(
                id="metric-success",
                run_id="run-1",
                name="Task Success",
                value=0.99,
                numerator=99,
                denominator=100,
                unit="ratio",
            )
        ],
        assertion_results=[_assertion("no-external-send", "blocked", hard=True)],
        failure_clusters=[],
    )

    assert gate.status == "blocked"
    assert gate.safety_blocking is True
    assert "safety hard gate failed" in gate.blocking_reasons


def test_run_comparator_reports_metric_deltas_and_new_failure_clusters() -> None:
    comparison = RunComparator().compare(
        baseline_metrics=[
            EvalMetric(
                id="old-success",
                run_id="old",
                name="Task Success",
                value=0.8,
                unit="ratio",
            )
        ],
        candidate_metrics=[
            EvalMetric(
                id="new-success",
                run_id="new",
                name="Task Success",
                value=0.9,
                unit="ratio",
            )
        ],
        baseline_cluster_keys={"case_error:case_timeout"},
        candidate_cluster_keys={"case_error:case_timeout", "assertion:approval:blocked"},
    )

    assert comparison["metric_deltas"]["Task Success"] == 0.1
    assert comparison["new_failure_clusters"] == ["assertion:approval:blocked"]
